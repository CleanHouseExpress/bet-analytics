from __future__ import annotations

import logging
from datetime import datetime, timezone
from time import perf_counter

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from apps.api.app.domain.features import FEATURE_ENGINE_VERSION, CompetitionBaseline, FeatureReason, FeatureSet, FormWindow, StrengthFeatures
from apps.api.app.domain.history import Match
from apps.api.app.domain.match_context import AnalysisType, CompetitionFormat, MatchContext

logger = logging.getLogger(__name__)
FINAL_STATUSES = frozenset({"finished"})


class FeatureEngineError(ValueError):
    def __init__(self, reason: FeatureReason):
        super().__init__(reason.value)
        self.reason = reason


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise FeatureEngineError(FeatureReason.INVALID_AS_OF)
    return value.astimezone(timezone.utc)


def _points(gf: int, ga: int) -> int:
    return 3 if gf > ga else 1 if gf == ga else 0


def _form(matches: list[Match], team_id: int, limit: int) -> FormWindow:
    sample = matches[:limit]
    if not sample:
        return FormWindow(0, None, None, None, 0, 0, 0, False)
    gf = ga = wins = draws = losses = 0
    for match in sample:
        if match.home_team_id == team_id:
            team_gf, team_ga = match.home_score, match.away_score
        else:
            team_gf, team_ga = match.away_score, match.home_score
        assert team_gf is not None and team_ga is not None
        gf += team_gf
        ga += team_ga
        pts = _points(team_gf, team_ga)
        wins += pts == 3
        draws += pts == 1
        losses += pts == 0
    games = len(sample)
    return FormWindow(games, gf / games, ga / games, (wins * 3 + draws) / games, wins, draws, losses, games == limit)


def _eligible_query(*, competition_id: int, season_id: int, as_of: datetime):
    return and_(
        Match.competition_id == competition_id,
        Match.season_id == season_id,
        Match.kickoff_at < as_of,
        Match.home_score.is_not(None),
        Match.away_score.is_not(None),
        Match.status.in_(FINAL_STATUSES),
        Match.finished_at.is_not(None),
        Match.finished_at <= as_of,
    )


class FeatureEngine:
    def __init__(self, session: Session):
        self.session = session

    def calculate(self, *, match_id: int, as_of: datetime, context: MatchContext) -> FeatureSet:
        started_at = perf_counter()
        as_of = _utc(as_of)
        target = self.session.get(Match, match_id)
        if target is None or context.match_id != match_id:
            raise FeatureEngineError(FeatureReason.INCOMPATIBLE_MATCH_CONTEXT)
        kickoff = _utc(target.kickoff_at)
        if as_of >= kickoff:
            raise FeatureEngineError(FeatureReason.INVALID_AS_OF)
        if (context.competition_format != CompetitionFormat.LEAGUE_POINTS or context.analysis_type != AnalysisType.PRE_MATCH or context.competition_id != target.competition_id or context.season_id != target.season_id or _utc(context.as_of) != as_of):
            raise FeatureEngineError(FeatureReason.INCOMPATIBLE_MATCH_CONTEXT)

        eligible = _eligible_query(competition_id=target.competition_id, season_id=target.season_id, as_of=as_of)
        ordering = (Match.kickoff_at.desc(), Match.id.desc())

        def history(team_id: int, *, venue: str | None = None) -> list[Match]:
            predicates = [eligible]
            if venue == "home":
                predicates.append(Match.home_team_id == team_id)
            elif venue == "away":
                predicates.append(Match.away_team_id == team_id)
            else:
                predicates.append(or_(Match.home_team_id == team_id, Match.away_team_id == team_id))
            stmt = select(Match).where(and_(*predicates), Match.id != match_id).order_by(*ordering).limit(10)
            return list(self.session.scalars(stmt))

        home_all = history(target.home_team_id)
        away_all = history(target.away_team_id)
        home_home = history(target.home_team_id, venue="home")
        away_away = history(target.away_team_id, venue="away")
        baseline_matches = list(self.session.scalars(select(Match).where(eligible, Match.id != match_id).order_by(*ordering)))
        if baseline_matches:
            games = len(baseline_matches)
            home_avg = sum(m.home_score or 0 for m in baseline_matches) / games
            away_avg = sum(m.away_score or 0 for m in baseline_matches) / games
            baseline = CompetitionBaseline(games, home_avg, away_avg, home_avg + away_avg)
        else:
            baseline = CompetitionBaseline(0, None, None, None)

        home_home10 = _form(home_home, target.home_team_id, 10)
        away_away10 = _form(away_away, target.away_team_id, 10)
        home_avg = baseline.home_goals_per_game
        away_avg = baseline.away_goals_per_game
        strengths = StrengthFeatures(
            home_attack=(home_home10.gf_per_game / home_avg) if home_home10.gf_per_game is not None and home_avg else None,
            home_defence_conceded=(home_home10.ga_per_game / away_avg) if home_home10.ga_per_game is not None and away_avg else None,
            away_attack=(away_away10.gf_per_game / away_avg) if away_away10.gf_per_game is not None and away_avg else None,
            away_defence_conceded=(away_away10.ga_per_game / home_avg) if away_away10.ga_per_game is not None and home_avg else None,
        )
        reasons: list[FeatureReason] = []
        if len(home_all) < 10 or len(away_all) < 10:
            reasons.append(FeatureReason.INSUFFICIENT_TEAM_HISTORY)
        if len(home_home) < 10:
            reasons.append(FeatureReason.INSUFFICIENT_HOME_HISTORY)
        if len(away_away) < 10:
            reasons.append(FeatureReason.INSUFFICIENT_AWAY_HISTORY)
        if not baseline_matches or not home_avg or not away_avg:
            reasons.append(FeatureReason.INSUFFICIENT_COMPETITION_BASELINE)

        result = FeatureSet(
            match_id=match_id, as_of=as_of, calculated_at=datetime.now(timezone.utc),
            context_classifier_version=context.classifier_version, feature_engine_version=FEATURE_ENGINE_VERSION,
            home_last5=_form(home_all, target.home_team_id, 5), home_last10=_form(home_all, target.home_team_id, 10),
            away_last5=_form(away_all, target.away_team_id, 5), away_last10=_form(away_all, target.away_team_id, 10),
            home_home5=_form(home_home, target.home_team_id, 5), home_home10=home_home10,
            away_away5=_form(away_away, target.away_team_id, 5), away_away10=away_away10,
            competition_baseline=baseline, strengths=strengths, reasons=tuple(dict.fromkeys(reasons)),
        )
        logger.info("feature_engine_calculated", extra={
            "match_id": match_id, "as_of": as_of.isoformat(), "feature_engine_version": FEATURE_ENGINE_VERSION,
            "home_history_count": len(home_all), "away_history_count": len(away_all),
            "home_split_count": len(home_home), "away_split_count": len(away_away),
            "competition_baseline_count": len(baseline_matches), "reasons": [reason.value for reason in result.reasons],
            "duration_ms": round((perf_counter() - started_at) * 1000, 3),
        })
        return result
