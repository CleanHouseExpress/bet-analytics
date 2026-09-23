from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select, text
from sqlalchemy.orm import Session

from apps.api.app.domain.backtest import (
    BACKTEST_VERSION,
    BacktestConfig,
    BacktestEvaluation,
    BacktestEvaluationStatus,
    BacktestMetrics,
    BacktestPromotionStatus,
    BacktestRun,
    BacktestSegment,
    CalibrationBucket,
)
from apps.api.app.domain.decision_journal import (
    AnalysisType as JournalAnalysisType,
    SettlementResult,
)
from apps.api.app.domain.features import (
    FEATURE_ENGINE_VERSION,
    CompetitionBaseline,
    FeatureReason,
    FeatureSet,
    StrengthFeatures,
)
from apps.api.app.domain.history import Match
from apps.api.app.domain.market_probability import Market
from apps.api.app.domain.match_context import CLASSIFIER_VERSION
from apps.api.app.domain.risk_assessment import OpenPosition, PositionStatus
from apps.api.app.domain.value_assessment import ValueDecision
from apps.api.app.services.decision_journal import DecisionJournal
from apps.api.app.services.feature_engine import _form
from apps.api.app.services.feature_snapshot import (
    semantic_hash as feature_semantic_hash,
)
from apps.api.app.services.feature_snapshot import semantic_payload as feature_semantic_payload
from apps.api.app.services.market_probability import (
    MarketProbabilityEngine,
    MarketProbabilityError,
)
from apps.api.app.services.market_probability_snapshot import (
    semantic_hash as market_probability_semantic_hash,
)
from apps.api.app.services.poisson_model import PoissonModel, PoissonModelError
from apps.api.app.services.poisson_snapshot import semantic_hash as poisson_semantic_hash
from apps.api.app.services.risk_engine import RiskEngine, RiskEngineError
from apps.api.app.services.value_assessment_snapshot import (
    semantic_hash as value_assessment_semantic_hash,
)
from apps.api.app.services.value_engine import ValueEngine, ValueEngineError

logger = logging.getLogger(__name__)

SAO_PAULO = ZoneInfo("America/Sao_Paulo")
GO_DECISIONS = frozenset(
    {
        ValueDecision.GO_CONDICIONAL,
        ValueDecision.GO,
        ValueDecision.GO_FORTE,
        ValueDecision.GO_PROTEGIDO,
    }
)
PROBABILITY_BANDS = (
    (0.00, 0.60, "<60%"),
    (0.60, 0.70, "60-69.9%"),
    (0.70, 0.75, "70-74.9%"),
    (0.75, 0.80, "75-79.9%"),
    (0.80, 0.85, "80-84.9%"),
    (0.85, 0.90, "85-89.9%"),
    (0.90, 1.01, "90-100%"),
)


class BacktestDataError(ValueError):
    pass


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise BacktestDataError("BACKTEST_REQUIRES_TIMEZONE_AWARE_DATETIME")
    return value.astimezone(UTC)


def _digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
        default=_json_default,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _market_outcome(market: Market, home_score: int, away_score: int) -> bool:
    total = home_score + away_score
    if market is Market.TOTAL_GOALS_OVER_1_5:
        return total > 1.5
    if market is Market.TOTAL_GOALS_OVER_2_5:
        return total > 2.5
    if market is Market.TOTAL_GOALS_UNDER_3_5:
        return total < 3.5
    if market is Market.TOTAL_GOALS_UNDER_4_5:
        return total < 4.5
    btts = home_score > 0 and away_score > 0
    if market is Market.BTTS_YES:
        return btts
    if market is Market.BTTS_NO:
        return not btts
    raise BacktestDataError(f"UNSUPPORTED_BACKTEST_MARKET:{market}")


def _clip_probability(value: float) -> float:
    return min(max(value, 1e-12), 1.0 - 1e-12)


def _brier(probability: float, actual: bool) -> float:
    target = 1.0 if actual else 0.0
    return (probability - target) ** 2


def _log_loss(probability: float, actual: bool) -> float:
    p = _clip_probability(probability)
    return -math.log(p if actual else 1.0 - p)


def _mean(values: list[float]) -> float | None:
    return math.fsum(values) / len(values) if values else None


def _probability_band(probability: float) -> str:
    for lower, upper, label in PROBABILITY_BANDS:
        if lower <= probability < upper:
            return label
    return "OUT_OF_RANGE"


def _odd_band(odd: float | None) -> str:
    if odd is None:
        return "MISSING"
    if odd < 1.20:
        return "<1.20"
    if odd < 1.30:
        return "1.20-1.29"
    if odd < 1.50:
        return "1.30-1.49"
    if odd < 2.00:
        return "1.50-1.99"
    return "2.00+"


def _normalized(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _line_matches(value: object, expected: float) -> bool:
    if value is None:
        return False
    try:
        return abs(float(value) - expected) <= 1e-9
    except (TypeError, ValueError):
        return False


def _odds_row_matches_market(row: dict[str, object], market: Market) -> bool:
    code = _normalized(row.get("market_code"))
    name = _normalized(row.get("market_name"))
    selection = _normalized(row.get("selection"))
    combined = f"{code}{name}{selection}"
    direct = _normalized(market.value)
    if code == direct or name == direct:
        return True

    if market in {
        Market.TOTAL_GOALS_OVER_1_5,
        Market.TOTAL_GOALS_OVER_2_5,
        Market.TOTAL_GOALS_UNDER_3_5,
        Market.TOTAL_GOALS_UNDER_4_5,
    }:
        line = (
            1.5
            if market is Market.TOTAL_GOALS_OVER_1_5
            else 2.5
            if market is Market.TOTAL_GOALS_OVER_2_5
            else 3.5
            if market is Market.TOTAL_GOALS_UNDER_3_5
            else 4.5
        )
        side = (
            "over"
            if market in {
                Market.TOTAL_GOALS_OVER_1_5,
                Market.TOTAL_GOALS_OVER_2_5,
            }
            else "under"
        )
        side_aliases = (
            ("over", "mais", "acima")
            if side == "over"
            else ("under", "menos", "abaixo")
        )
        has_total = any(token in combined for token in ("total", "goal", "gols"))
        has_side = any(token in combined for token in side_aliases)
        return has_total and has_side and _line_matches(row.get("line"), line)

    if market in {Market.BTTS_YES, Market.BTTS_NO}:
        has_btts = any(
            token in combined
            for token in ("btts", "bothteamstoscore", "ambasmarcam")
        )
        if not has_btts:
            return False
        if market is Market.BTTS_YES:
            return selection in {"yes", "sim", "y"} or "yes" in code or "sim" in code
        return selection in {"no", "nao", "n"} or "no" in code or "nao" in code
    return False


def _historical_settled_at(
    kickoff_at: datetime,
    finished_at: datetime | None,
) -> datetime:
    kickoff = _utc(kickoff_at)
    if finished_at is not None:
        observed = _utc(finished_at)
        if kickoff <= observed <= kickoff + timedelta(hours=12):
            return observed
    # Historical imports can carry the ingestion timestamp in finished_at. For
    # chronological simulation, use a conservative event-time completion bound.
    return kickoff + timedelta(hours=3)


class HistoricalFeatureEngine:
    """Backtest-only reconstruction using event chronology rather than ingestion time."""

    def __init__(self, session: Session):
        self.session = session

    def calculate(self, *, match_id: int, as_of: datetime) -> FeatureSet:
        as_of = _utc(as_of)
        target = self.session.get(Match, match_id)
        if target is None:
            raise BacktestDataError("BACKTEST_MATCH_NOT_FOUND")
        if as_of >= _utc(target.kickoff_at):
            raise BacktestDataError("BACKTEST_INVALID_AS_OF")

        eligible = and_(
            Match.competition_id == target.competition_id,
            Match.season_id == target.season_id,
            Match.kickoff_at < as_of,
            Match.home_score.is_not(None),
            Match.away_score.is_not(None),
            Match.status == "finished",
        )
        ordering = (Match.kickoff_at.desc(), Match.id.desc())

        def history(team_id: int, *, venue: str | None = None) -> list[Match]:
            predicates = [eligible]
            if venue == "home":
                predicates.append(Match.home_team_id == team_id)
            elif venue == "away":
                predicates.append(Match.away_team_id == team_id)
            else:
                predicates.append(
                    or_(
                        Match.home_team_id == team_id,
                        Match.away_team_id == team_id,
                    )
                )
            statement = (
                select(Match)
                .where(and_(*predicates), Match.id != match_id)
                .order_by(*ordering)
                .limit(10)
            )
            return list(self.session.scalars(statement))

        home_all = history(target.home_team_id)
        away_all = history(target.away_team_id)
        home_home = history(target.home_team_id, venue="home")
        away_away = history(target.away_team_id, venue="away")
        baseline_matches = list(
            self.session.scalars(
                select(Match)
                .where(eligible, Match.id != match_id)
                .order_by(*ordering)
            )
        )

        if baseline_matches:
            games = len(baseline_matches)
            home_average = math.fsum(
                float(match.home_score or 0) for match in baseline_matches
            ) / games
            away_average = math.fsum(
                float(match.away_score or 0) for match in baseline_matches
            ) / games
            baseline = CompetitionBaseline(
                games,
                home_average,
                away_average,
                home_average + away_average,
            )
        else:
            baseline = CompetitionBaseline(0, None, None, None)

        home_home10 = _form(home_home, target.home_team_id, 10)
        away_away10 = _form(away_away, target.away_team_id, 10)
        home_average = baseline.home_goals_per_game
        away_average = baseline.away_goals_per_game
        strengths = StrengthFeatures(
            home_attack=(
                home_home10.gf_per_game / home_average
                if home_home10.gf_per_game is not None and home_average
                else None
            ),
            home_defence_conceded=(
                home_home10.ga_per_game / away_average
                if home_home10.ga_per_game is not None and away_average
                else None
            ),
            away_attack=(
                away_away10.gf_per_game / away_average
                if away_away10.gf_per_game is not None and away_average
                else None
            ),
            away_defence_conceded=(
                away_away10.ga_per_game / home_average
                if away_away10.ga_per_game is not None and home_average
                else None
            ),
        )
        reasons: list[FeatureReason] = []
        if len(home_all) < 10 or len(away_all) < 10:
            reasons.append(FeatureReason.INSUFFICIENT_TEAM_HISTORY)
        if len(home_home) < 10:
            reasons.append(FeatureReason.INSUFFICIENT_HOME_HISTORY)
        if len(away_away) < 10:
            reasons.append(FeatureReason.INSUFFICIENT_AWAY_HISTORY)
        if not baseline_matches or not home_average or not away_average:
            reasons.append(FeatureReason.INSUFFICIENT_COMPETITION_BASELINE)

        return FeatureSet(
            match_id=match_id,
            as_of=as_of,
            calculated_at=datetime.now(UTC),
            context_classifier_version=CLASSIFIER_VERSION,
            feature_engine_version=FEATURE_ENGINE_VERSION,
            home_last5=_form(home_all, target.home_team_id, 5),
            home_last10=_form(home_all, target.home_team_id, 10),
            away_last5=_form(away_all, target.away_team_id, 5),
            away_last10=_form(away_all, target.away_team_id, 10),
            home_home5=_form(home_home, target.home_team_id, 5),
            home_home10=home_home10,
            away_away5=_form(away_away, target.away_team_id, 5),
            away_away10=away_away10,
            competition_baseline=baseline,
            strengths=strengths,
            reasons=tuple(dict.fromkeys(reasons)),
        )


class WalkForwardBacktest:
    def __init__(self, session: Session):
        self.session = session

    def run(self, config: BacktestConfig = BacktestConfig()) -> BacktestRun:
        self._validate_config(config)
        matches = self._load_matches(config)
        self._validate_match_set(matches)
        relevant_odds = self._load_relevant_odds(matches, config.markets)
        data_fingerprint = self._data_fingerprint(matches, relevant_odds, config)

        rounds: dict[tuple[str, int], list[dict[str, object]]] = defaultdict(list)
        for match in matches:
            rounds[(str(match["season"]), int(match["round_number"]))].append(match)

        bankroll = float(config.initial_bankroll)
        equity_curve = [bankroll]
        pending_cashflows: list[tuple[datetime, float]] = []
        evaluations: list[BacktestEvaluation] = []
        historical_features = HistoricalFeatureEngine(self.session)

        ordered_rounds = sorted(
            rounds.items(),
            key=lambda item: (
                min(_utc(row["kickoff_at"]) for row in item[1]),
                item[0][0],
                item[0][1],
            ),
        )

        for (_, round_number), round_matches in ordered_rounds:
            round_as_of = min(
                _utc(row["kickoff_at"]) for row in round_matches
            ) - timedelta(seconds=config.as_of_offset_seconds)
            bankroll, pending_cashflows = self._apply_pending_cashflows(
                bankroll,
                pending_cashflows,
                round_as_of,
                equity_curve,
            )
            baseline = self._baseline_probabilities(
                matches,
                markets=config.markets,
                as_of=round_as_of,
            )
            round_bankroll = bankroll

            for match in sorted(
                round_matches,
                key=lambda row: (_utc(row["kickoff_at"]), int(row["match_id"])),
            ):
                match_evaluations = self._evaluate_match(
                    config=config,
                    match=match,
                    round_number=round_number,
                    round_as_of=round_as_of,
                    bankroll=round_bankroll,
                    baseline=baseline,
                    odds_rows=relevant_odds.get(int(match["match_id"]), ()),
                    historical_features=historical_features,
                )
                evaluations.extend(match_evaluations)
                for evaluation in match_evaluations:
                    if (
                        evaluation.profit_loss is not None
                        and evaluation.settled_at is not None
                        and evaluation.stake_value > 0
                    ):
                        pending_cashflows.append(
                            (evaluation.settled_at, evaluation.profit_loss)
                        )

        for settled_at, profit_loss in sorted(
            pending_cashflows,
            key=lambda item: item[0],
        ):
            _ = settled_at
            bankroll += profit_loss
            equity_curve.append(bankroll)

        metrics = self._metrics(
            evaluations,
            initial_bankroll=float(config.initial_bankroll),
            ending_bankroll=bankroll,
            equity_curve=equity_curve,
            min_sample_for_review=config.min_sample_for_review,
        )
        segments = self._segments(evaluations)
        semantic = {
            "backtest_version": BACKTEST_VERSION,
            "config": asdict(config),
            "data_fingerprint": data_fingerprint,
            "evaluations": [
                self._evaluation_semantic_payload(evaluation)
                for evaluation in evaluations
            ],
            "metrics": asdict(metrics),
            "segments": [asdict(segment) for segment in segments],
        }
        semantic_hash = _digest(semantic)
        result = BacktestRun(
            run_id=semantic_hash,
            semantic_hash=semantic_hash,
            data_fingerprint=data_fingerprint,
            generated_at=datetime.now(UTC),
            backtest_version=BACKTEST_VERSION,
            config=config,
            evaluations=tuple(evaluations),
            metrics=metrics,
            segments=segments,
            equity_curve=tuple(equity_curve),
        )
        logger.info(
            "walk_forward_backtest_completed",
            extra={
                "run_id": result.run_id,
                "backtest_version": BACKTEST_VERSION,
                "evaluations": metrics.evaluations,
                "probability_evaluations": metrics.probability_evaluations,
                "bets": metrics.bets,
                "profit_loss": metrics.profit_loss,
                "yield_on_stake": metrics.yield_on_stake,
                "promotion_status": metrics.promotion_status.value,
            },
        )
        return result

    @staticmethod
    def _validate_config(config: BacktestConfig) -> None:
        if not isinstance(config, BacktestConfig):
            raise BacktestDataError("INVALID_BACKTEST_CONFIG")
        if (
            not config.competition_name.strip()
            or not config.seasons
            or not config.markets
            or not math.isfinite(config.initial_bankroll)
            or config.initial_bankroll <= 0
            or not math.isfinite(config.uncertainty_margin_pp)
            or not 2.0 <= config.uncertainty_margin_pp <= 5.0
            or config.as_of_offset_seconds <= 0
            or config.min_sample_for_review <= 0
        ):
            raise BacktestDataError("INVALID_BACKTEST_CONFIG")
        if len(set(config.seasons)) != len(config.seasons):
            raise BacktestDataError("DUPLICATE_BACKTEST_SEASON")
        if len(set(config.markets)) != len(config.markets):
            raise BacktestDataError("DUPLICATE_BACKTEST_MARKET")

    def _load_matches(self, config: BacktestConfig) -> list[dict[str, object]]:
        rows = self.session.execute(
            text(
                """
                SELECT
                    m.id AS match_id,
                    m.competition_id,
                    m.season_id,
                    s.name AS season,
                    r.round_number,
                    m.home_team_id,
                    m.away_team_id,
                    m.kickoff_at,
                    m.finished_at,
                    m.home_score,
                    m.away_score
                FROM matches m
                JOIN competitions c ON c.id = m.competition_id
                JOIN seasons s ON s.id = m.season_id
                LEFT JOIN rounds r ON r.id = m.round_id
                WHERE c.name = :competition
                  AND s.name = ANY(CAST(:seasons AS text[]))
                  AND c.competition_type = 'league'
                  AND m.status = 'finished'
                  AND m.home_score IS NOT NULL
                  AND m.away_score IS NOT NULL
                ORDER BY m.kickoff_at, m.id
                """
            ),
            {
                "competition": config.competition_name,
                "seasons": list(config.seasons),
            },
        ).mappings().all()
        if not rows:
            raise BacktestDataError("NO_BACKTEST_MATCHES")
        return [dict(row) for row in rows]

    @staticmethod
    def _validate_match_set(matches: list[dict[str, object]]) -> None:
        fixture_identity: set[tuple[object, ...]] = set()
        ordered_pair: set[tuple[object, ...]] = set()
        round_team: set[tuple[object, ...]] = set()

        for row in matches:
            if row["round_number"] is None:
                raise BacktestDataError(
                    f"MISSING_ROUND_NUMBER:match_id={row['match_id']}"
                )
            local_date = _utc(row["kickoff_at"]).astimezone(SAO_PAULO).date()
            identity = (
                row["season"],
                local_date.isoformat(),
                row["home_team_id"],
                row["away_team_id"],
            )
            if identity in fixture_identity:
                raise BacktestDataError(f"DUPLICATE_FIXTURE:{identity}")
            fixture_identity.add(identity)

            pair = (row["season"], row["home_team_id"], row["away_team_id"])
            if pair in ordered_pair:
                raise BacktestDataError(f"DUPLICATE_ORDERED_FIXTURE:{pair}")
            ordered_pair.add(pair)

            for team_id in (row["home_team_id"], row["away_team_id"]):
                key = (row["season"], row["round_number"], team_id)
                if key in round_team:
                    raise BacktestDataError(f"DUPLICATE_TEAM_IN_ROUND:{key}")
                round_team.add(key)

    def _load_relevant_odds(
        self,
        matches: list[dict[str, object]],
        markets: tuple[Market, ...],
    ) -> dict[int, tuple[dict[str, object], ...]]:
        ids = [int(row["match_id"]) for row in matches]
        rows = self.session.execute(
            text(
                """
                SELECT
                    os.match_id,
                    os.odd,
                    os.line,
                    os.selection,
                    os.captured_at,
                    m.code AS market_code,
                    m.name AS market_name,
                    m.category AS market_category,
                    b.name AS bookmaker_name
                FROM odds_snapshots os
                JOIN markets m ON m.id = os.market_id
                JOIN bookmakers b ON b.id = os.bookmaker_id
                WHERE os.match_id = ANY(CAST(:match_ids AS bigint[]))
                  AND os.odd > 1
                ORDER BY os.match_id, os.captured_at, os.id
                """
            ),
            {"match_ids": ids},
        ).mappings().all()

        grouped: dict[int, list[dict[str, object]]] = defaultdict(list)
        for row in rows:
            item = dict(row)
            if any(_odds_row_matches_market(item, market) for market in markets):
                grouped[int(item["match_id"])].append(item)
        return {match_id: tuple(items) for match_id, items in grouped.items()}

    @staticmethod
    def _data_fingerprint(
        matches: list[dict[str, object]],
        odds: dict[int, tuple[dict[str, object], ...]],
        config: BacktestConfig,
    ) -> str:
        match_payload = [
            {
                key: row[key]
                for key in (
                    "match_id",
                    "competition_id",
                    "season_id",
                    "season",
                    "round_number",
                    "home_team_id",
                    "away_team_id",
                    "kickoff_at",
                    "finished_at",
                    "home_score",
                    "away_score",
                )
            }
            for row in matches
        ]
        odds_payload = [
            item
            for match_id in sorted(odds)
            for item in odds[match_id]
        ]
        return _digest(
            {
                "competition": config.competition_name,
                "seasons": config.seasons,
                "markets": config.markets,
                "matches": match_payload,
                "odds": odds_payload,
            }
        )

    def _baseline_probabilities(
        self,
        matches: list[dict[str, object]],
        *,
        markets: tuple[Market, ...],
        as_of: datetime,
    ) -> dict[Market, float | None]:
        previous = [
            row
            for row in matches
            if _historical_settled_at(row["kickoff_at"], row["finished_at"]) <= as_of
        ]
        result: dict[Market, float | None] = {}
        for market in markets:
            outcomes = [
                _market_outcome(
                    market,
                    int(row["home_score"]),
                    int(row["away_score"]),
                )
                for row in previous
            ]
            result[market] = (
                math.fsum(1.0 if outcome else 0.0 for outcome in outcomes)
                / len(outcomes)
                if outcomes
                else None
            )
        return result

    def _evaluate_match(
        self,
        *,
        config: BacktestConfig,
        match: dict[str, object],
        round_number: int,
        round_as_of: datetime,
        bankroll: float,
        baseline: dict[Market, float | None],
        odds_rows: tuple[dict[str, object], ...],
        historical_features: HistoricalFeatureEngine,
    ) -> list[BacktestEvaluation]:
        match_id = int(match["match_id"])
        kickoff_at = _utc(match["kickoff_at"])
        settled_at = _historical_settled_at(kickoff_at, match["finished_at"])
        home_score = int(match["home_score"])
        away_score = int(match["away_score"])
        positions: list[OpenPosition] = []

        try:
            features = historical_features.calculate(
                match_id=match_id,
                as_of=round_as_of,
            )
            poisson = PoissonModel().calculate(features=features)
        except (BacktestDataError, PoissonModelError) as exc:
            reason = str(exc)
            return [
                self._blocked_evaluation(
                    config=config,
                    match=match,
                    round_number=round_number,
                    round_as_of=round_as_of,
                    market=market,
                    baseline_probability=baseline.get(market),
                    actual_outcome=_market_outcome(market, home_score, away_score),
                    settled_at=settled_at,
                    reason=reason,
                )
                for market in config.markets
            ]

        feature_hash = feature_semantic_hash(features)
        poisson_hash = poisson_semantic_hash(poisson)
        feature_payload = json.dumps(
            feature_semantic_payload(features),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        model_side = (
            "HOME"
            if poisson.lambda_home > poisson.lambda_away + 1e-12
            else "AWAY"
            if poisson.lambda_away > poisson.lambda_home + 1e-12
            else "BALANCED"
        )

        evaluations: list[BacktestEvaluation] = []
        price_cutoff = kickoff_at - timedelta(seconds=config.as_of_offset_seconds)
        for market in config.markets:
            actual = _market_outcome(market, home_score, away_score)
            try:
                probability = MarketProbabilityEngine().calculate(
                    poisson=poisson,
                    market=market,
                )
            except MarketProbabilityError as exc:
                evaluations.append(
                    self._blocked_evaluation(
                        config=config,
                        match=match,
                        round_number=round_number,
                        round_as_of=round_as_of,
                        market=market,
                        baseline_probability=baseline.get(market),
                        actual_outcome=actual,
                        settled_at=settled_at,
                        reason=str(exc),
                        feature_hash=feature_hash,
                        poisson_hash=poisson_hash,
                        feature_payload=feature_payload,
                        model_side=model_side,
                    )
                )
                continue

            probability_hash = market_probability_semantic_hash(probability)
            price = self._market_price(
                odds_rows,
                market=market,
                cutoff=price_cutoff,
            )
            if price is None:
                evaluations.append(
                    BacktestEvaluation(
                        match_id=match_id,
                        season=str(match["season"]),
                        round_number=round_number,
                        kickoff_at=kickoff_at,
                        as_of=round_as_of,
                        market=market,
                        status=BacktestEvaluationStatus.MISSING_ODD,
                        p_model=probability.p_model,
                        baseline_probability=baseline.get(market),
                        actual_outcome=actual,
                        model_side=model_side,
                        market_odd=None,
                        odd_source=None,
                        odd_observed_at=None,
                        value_decision=None,
                        risk_decision=None,
                        stake_units=0.0,
                        stake_value=0.0,
                        settlement_result=None,
                        settled_at=settled_at,
                        profit_loss=None,
                        journal_entry=None,
                        feature_semantic_hash=feature_hash,
                        poisson_semantic_hash=poisson_hash,
                        market_probability_semantic_hash=probability_hash,
                        value_semantic_hash=None,
                        risk_semantic_hash=None,
                        feature_payload=feature_payload,
                        block_reason="MISSING_MARKET_ODD",
                    )
                )
                continue

            market_odd, odd_source, odd_observed_at = price
            try:
                value = ValueEngine().calculate(
                    probability=probability,
                    market_odd=market_odd,
                    uncertainty_margin_pp=config.uncertainty_margin_pp,
                    odd_source=odd_source,
                    odd_observed_at=odd_observed_at,
                )
                risk = RiskEngine().calculate(
                    value=value,
                    bankroll_amount=bankroll,
                    positions=tuple(positions),
                    exposure_known=True,
                )
                journal = DecisionJournal().record(
                    features=features,
                    poisson=poisson,
                    probability=probability,
                    value=value,
                    risk=risk,
                    analysis_type=JournalAnalysisType.PRE_MATCH,
                    match_type="LEAGUE",
                    competition=config.competition_name,
                    thesis=config.thesis,
                    evaluated_at=round_as_of,
                    positions=tuple(positions),
                )
            except (ValueEngineError, RiskEngineError, ValueError) as exc:
                evaluations.append(
                    self._blocked_evaluation(
                        config=config,
                        match=match,
                        round_number=round_number,
                        round_as_of=round_as_of,
                        market=market,
                        baseline_probability=baseline.get(market),
                        actual_outcome=actual,
                        settled_at=settled_at,
                        reason=str(exc),
                        p_model=probability.p_model,
                        market_odd=market_odd,
                        odd_source=odd_source,
                        odd_observed_at=odd_observed_at,
                        feature_hash=feature_hash,
                        poisson_hash=poisson_hash,
                        probability_hash=probability_hash,
                        feature_payload=feature_payload,
                        model_side=model_side,
                    )
                )
                continue

            settlement_result = None
            profit_loss = None
            if risk.stake_amount > 0:
                settlement_result = (
                    SettlementResult.GREEN if actual else SettlementResult.RED
                )
                profit_loss = (
                    risk.stake_amount * (market_odd - 1.0)
                    if actual
                    else -risk.stake_amount
                )
                positions.append(
                    OpenPosition(
                        position_id=journal.journal_entry_id,
                        match_id=match_id,
                        market=market,
                        stake_units=risk.final_stake_units,
                        status=PositionStatus.PLACED,
                        wallet_id="BETS-9",
                    )
                )

            evaluations.append(
                BacktestEvaluation(
                    match_id=match_id,
                    season=str(match["season"]),
                    round_number=round_number,
                    kickoff_at=kickoff_at,
                    as_of=round_as_of,
                    market=market,
                    status=BacktestEvaluationStatus.EVALUATED,
                    p_model=probability.p_model,
                    baseline_probability=baseline.get(market),
                    actual_outcome=actual,
                    model_side=model_side,
                    market_odd=market_odd,
                    odd_source=odd_source,
                    odd_observed_at=odd_observed_at,
                    value_decision=value.decision,
                    risk_decision=risk.risk_decision,
                    stake_units=risk.final_stake_units,
                    stake_value=risk.stake_amount,
                    settlement_result=settlement_result,
                    settled_at=settled_at,
                    profit_loss=profit_loss,
                    journal_entry=journal,
                    feature_semantic_hash=feature_hash,
                    poisson_semantic_hash=poisson_hash,
                    market_probability_semantic_hash=probability_hash,
                    value_semantic_hash=value_assessment_semantic_hash(value),
                    risk_semantic_hash=risk.semantic_hash,
                    feature_payload=feature_payload,
                    block_reason=None,
                )
            )
        return evaluations

    @staticmethod
    def _market_price(
        rows: tuple[dict[str, object], ...],
        *,
        market: Market,
        cutoff: datetime,
    ) -> tuple[float, str, datetime] | None:
        candidates = [
            row
            for row in rows
            if _utc(row["captured_at"]) <= cutoff
            and _odds_row_matches_market(row, market)
        ]
        if not candidates:
            return None
        latest = max(_utc(row["captured_at"]) for row in candidates)
        at_latest = [
            row for row in candidates if _utc(row["captured_at"]) == latest
        ]
        best = max(at_latest, key=lambda row: (float(row["odd"]), str(row["bookmaker_name"])))
        return (
            float(best["odd"]),
            str(best["bookmaker_name"]),
            latest,
        )

    @staticmethod
    def _blocked_evaluation(
        *,
        config: BacktestConfig,
        match: dict[str, object],
        round_number: int,
        round_as_of: datetime,
        market: Market,
        baseline_probability: float | None,
        actual_outcome: bool,
        settled_at: datetime,
        reason: str,
        p_model: float | None = None,
        market_odd: float | None = None,
        odd_source: str | None = None,
        odd_observed_at: datetime | None = None,
        feature_hash: str | None = None,
        poisson_hash: str | None = None,
        probability_hash: str | None = None,
        feature_payload: str | None = None,
        model_side: str | None = None,
    ) -> BacktestEvaluation:
        _ = config
        return BacktestEvaluation(
            match_id=int(match["match_id"]),
            season=str(match["season"]),
            round_number=round_number,
            kickoff_at=_utc(match["kickoff_at"]),
            as_of=round_as_of,
            market=market,
            status=BacktestEvaluationStatus.BLOCKED,
            p_model=p_model,
            baseline_probability=baseline_probability,
            actual_outcome=actual_outcome,
            model_side=model_side,
            market_odd=market_odd,
            odd_source=odd_source,
            odd_observed_at=odd_observed_at,
            value_decision=None,
            risk_decision=None,
            stake_units=0.0,
            stake_value=0.0,
            settlement_result=None,
            settled_at=settled_at,
            profit_loss=None,
            journal_entry=None,
            feature_semantic_hash=feature_hash,
            poisson_semantic_hash=poisson_hash,
            market_probability_semantic_hash=probability_hash,
            value_semantic_hash=None,
            risk_semantic_hash=None,
            feature_payload=feature_payload,
            block_reason=reason,
        )

    @staticmethod
    def _apply_pending_cashflows(
        bankroll: float,
        pending: list[tuple[datetime, float]],
        cutoff: datetime,
        equity_curve: list[float],
    ) -> tuple[float, list[tuple[datetime, float]]]:
        remaining: list[tuple[datetime, float]] = []
        for settled_at, profit_loss in sorted(pending, key=lambda item: item[0]):
            if settled_at <= cutoff:
                bankroll += profit_loss
                equity_curve.append(bankroll)
            else:
                remaining.append((settled_at, profit_loss))
        return bankroll, remaining

    @staticmethod
    def _metrics(
        evaluations: list[BacktestEvaluation],
        *,
        initial_bankroll: float,
        ending_bankroll: float,
        equity_curve: list[float],
        min_sample_for_review: int,
    ) -> BacktestMetrics:
        predictive = [item for item in evaluations if item.p_model is not None]
        priced = [
            item
            for item in evaluations
            if item.status is BacktestEvaluationStatus.EVALUATED
            and item.value_decision is not None
        ]
        bets = [item for item in priced if item.stake_value > 0]
        brier_values = [
            _brier(float(item.p_model), item.actual_outcome) for item in predictive
        ]
        log_values = [
            _log_loss(float(item.p_model), item.actual_outcome) for item in predictive
        ]
        baseline_pairs = [
            item
            for item in predictive
            if item.baseline_probability is not None
        ]
        baseline_brier = [
            _brier(float(item.baseline_probability), item.actual_outcome)
            for item in baseline_pairs
        ]
        baseline_log = [
            _log_loss(float(item.baseline_probability), item.actual_outcome)
            for item in baseline_pairs
        ]

        calibration = WalkForwardBacktest._calibration(predictive)
        ece = (
            math.fsum(
                bucket.absolute_gap * bucket.count
                for bucket in calibration
                if bucket.absolute_gap is not None
            )
            / len(predictive)
            if predictive
            else None
        )
        correct = sum(
            (float(item.p_model) >= 0.5) == item.actual_outcome
            for item in predictive
        )
        stake_total = math.fsum(item.stake_value for item in bets)
        profit_loss = math.fsum(float(item.profit_loss or 0.0) for item in bets)
        peak = equity_curve[0] if equity_curve else initial_bankroll
        max_drawdown = 0.0
        for equity in equity_curve:
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)

        brier_score = _mean(brier_values)
        log_loss = _mean(log_values)
        baseline_brier_score = _mean(baseline_brier)
        baseline_log_loss = _mean(baseline_log)
        yield_on_stake = profit_loss / stake_total if stake_total > 0 else None

        if len(bets) < min_sample_for_review:
            promotion_status = BacktestPromotionStatus.INSUFFICIENT_SAMPLE
        elif (
            brier_score is None
            or log_loss is None
            or baseline_brier_score is None
            or baseline_log_loss is None
            or brier_score >= baseline_brier_score
            or log_loss >= baseline_log_loss
        ):
            promotion_status = BacktestPromotionStatus.NOT_BETTER_THAN_BASELINE
        elif profit_loss <= 0 or yield_on_stake is None or yield_on_stake <= 0:
            promotion_status = (
                BacktestPromotionStatus.NEGATIVE_FINANCIAL_PERFORMANCE
            )
        else:
            promotion_status = BacktestPromotionStatus.ELIGIBLE_FOR_REVIEW

        return BacktestMetrics(
            evaluations=len(evaluations),
            probability_evaluations=len(predictive),
            blocked_evaluations=sum(
                item.status is BacktestEvaluationStatus.BLOCKED
                for item in evaluations
            ),
            missing_odd_evaluations=sum(
                item.status is BacktestEvaluationStatus.MISSING_ODD
                for item in evaluations
            ),
            no_go_decisions=sum(
                item.value_decision is ValueDecision.NO_GO for item in priced
            ),
            observe_decisions=sum(
                item.value_decision is ValueDecision.OBSERVAR for item in priced
            ),
            go_decisions=sum(item.value_decision in GO_DECISIONS for item in priced),
            bets=len(bets),
            wins=sum(item.settlement_result is SettlementResult.GREEN for item in bets),
            losses=sum(item.settlement_result is SettlementResult.RED for item in bets),
            accuracy=(correct / len(predictive)) if predictive else None,
            brier_score=brier_score,
            log_loss=log_loss,
            baseline_brier_score=baseline_brier_score,
            baseline_log_loss=baseline_log_loss,
            expected_calibration_error=ece,
            stake_total=stake_total,
            profit_loss=profit_loss,
            roi_on_bankroll=profit_loss / initial_bankroll,
            yield_on_stake=yield_on_stake,
            max_drawdown=max_drawdown,
            initial_bankroll=initial_bankroll,
            ending_bankroll=ending_bankroll,
            promotion_status=promotion_status,
            calibration=calibration,
        )

    @staticmethod
    def _calibration(
        evaluations: list[BacktestEvaluation],
    ) -> tuple[CalibrationBucket, ...]:
        buckets: list[CalibrationBucket] = []
        for index in range(10):
            lower = index / 10.0
            upper = (index + 1) / 10.0
            items = [
                item
                for item in evaluations
                if item.p_model is not None
                and (
                    lower <= float(item.p_model) < upper
                    or (index == 9 and float(item.p_model) == 1.0)
                )
            ]
            probabilities = [float(item.p_model) for item in items]
            observed = [
                1.0 if item.actual_outcome else 0.0 for item in items
            ]
            mean_probability = _mean(probabilities)
            observed_rate = _mean(observed)
            gap = (
                abs(mean_probability - observed_rate)
                if mean_probability is not None and observed_rate is not None
                else None
            )
            buckets.append(
                CalibrationBucket(
                    label=f"{int(lower * 100)}-{int(upper * 100)}%",
                    lower_bound=lower,
                    upper_bound=upper,
                    count=len(items),
                    mean_probability=mean_probability,
                    observed_rate=observed_rate,
                    absolute_gap=gap,
                )
            )
        return tuple(buckets)

    @staticmethod
    def _segments(
        evaluations: list[BacktestEvaluation],
    ) -> tuple[BacktestSegment, ...]:
        dimensions: dict[str, dict[str, list[BacktestEvaluation]]] = {
            "market": defaultdict(list),
            "season": defaultdict(list),
            "probability_band": defaultdict(list),
            "odd_band": defaultdict(list),
            "model_side": defaultdict(list),
        }
        for item in evaluations:
            if item.p_model is None:
                continue
            dimensions["market"][item.market.value].append(item)
            dimensions["season"][item.season].append(item)
            dimensions["probability_band"][
                _probability_band(float(item.p_model))
            ].append(item)
            dimensions["odd_band"][_odd_band(item.market_odd)].append(item)
            dimensions["model_side"][item.model_side or "UNKNOWN"].append(item)

        segments: list[BacktestSegment] = []
        for dimension, groups in dimensions.items():
            for value, items in sorted(groups.items()):
                bets = [item for item in items if item.stake_value > 0]
                brier_score = _mean(
                    [_brier(float(item.p_model), item.actual_outcome) for item in items]
                )
                baseline_items = [
                    item for item in items if item.baseline_probability is not None
                ]
                baseline_brier = _mean(
                    [
                        _brier(
                            float(item.baseline_probability),
                            item.actual_outcome,
                        )
                        for item in baseline_items
                    ]
                )
                stake_total = math.fsum(item.stake_value for item in bets)
                profit_loss = math.fsum(
                    float(item.profit_loss or 0.0) for item in bets
                )
                segments.append(
                    BacktestSegment(
                        dimension=dimension,
                        value=value,
                        evaluations=len(items),
                        bets=len(bets),
                        hit_rate=(
                            sum(
                                item.settlement_result is SettlementResult.GREEN
                                for item in bets
                            )
                            / len(bets)
                            if bets
                            else None
                        ),
                        brier_score=brier_score,
                        baseline_brier_score=baseline_brier,
                        stake_total=stake_total,
                        profit_loss=profit_loss,
                        yield_on_stake=(
                            profit_loss / stake_total if stake_total > 0 else None
                        ),
                    )
                )
        return tuple(segments)

    @staticmethod
    def _evaluation_semantic_payload(
        evaluation: BacktestEvaluation,
    ) -> dict[str, object]:
        payload = asdict(evaluation)
        journal = payload.get("journal_entry")
        if isinstance(journal, dict):
            journal.pop("evaluated_at", None)
        return payload
