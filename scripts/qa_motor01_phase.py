from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from sqlalchemy import text

from apps.api.app.core.database import SessionLocal
from apps.api.app.domain.backtest import BacktestConfig
from apps.api.app.domain.decision_journal import AnalysisType as JournalAnalysisType
from apps.api.app.domain.match_context import AnalysisType as ContextAnalysisType
from apps.api.app.domain.value_assessment import ValueDecision, ValueReason
from apps.api.app.services.backtest_snapshot import persist_backtest_run
from apps.api.app.services.brasileirao_csv_import import BrasileiraoCsvImporter
from apps.api.app.services.decision_journal import DecisionJournal
from apps.api.app.services.decision_journal_snapshot import persist_decision_journal_entry
from apps.api.app.services.market_probability import MarketProbabilityEngine
from apps.api.app.services.match_context import MatchContextClassifier
from apps.api.app.services.poisson_model import PoissonModel
from apps.api.app.services.risk_engine import RiskEngine
from apps.api.app.services.value_engine import ValueEngine, ValueEngineError
from apps.api.app.services.walk_forward_backtest import HistoricalFeatureEngine, WalkForwardBacktest


CSV_PATHS = (
    Path("docs/brasileirao_serie_a_2024_enriched_v2.csv"),
    Path("docs/brasileirao_serie_a_2025_enriched_v6.csv"),
    Path("docs/brasileirao_serie_a_2026_enriched_v5.csv"),
)
EXPECTED_ROWS = {"2024": 380, "2025": 380, "2026": 215}
CONTROLLED_ODD = 2.50


def _season_counts(session) -> dict[str, dict[str, int]]:
    rows = session.execute(
        text(
            """
            SELECT s.name AS season,
                   count(DISTINCT m.id) AS matches,
                   count(DISTINCT m.home_team_id) + 0 AS home_teams
            FROM seasons s
            JOIN competitions c ON c.id = s.competition_id
            JOIN matches m ON m.season_id = s.id
            WHERE c.name = 'Brasileirão Série A'
              AND s.name IN ('2024','2025','2026')
            GROUP BY s.name
            ORDER BY s.name
            """
        )
    ).mappings().all()
    result = {
        row["season"]: {
            "matches": int(row["matches"]),
            "home_teams": int(row["home_teams"]),
        }
        for row in rows
    }
    for season in EXPECTED_ROWS:
        team_count = session.execute(
            text(
                """
                SELECT count(DISTINCT team_id)
                FROM (
                    SELECT m.home_team_id AS team_id
                    FROM matches m
                    JOIN seasons s ON s.id=m.season_id
                    JOIN competitions c ON c.id=s.competition_id
                    WHERE c.name='Brasileirão Série A' AND s.name=:season
                    UNION
                    SELECT m.away_team_id AS team_id
                    FROM matches m
                    JOIN seasons s ON s.id=m.season_id
                    JOIN competitions c ON c.id=s.competition_id
                    WHERE c.name='Brasileirão Série A' AND s.name=:season
                ) q
                """
            ),
            {"season": season},
        ).scalar_one()
        result[season]["teams"] = int(team_count)
    return result


def _pick_real_chain(session):
    candidates = session.execute(
        text(
            """
            SELECT m.id, m.kickoff_at, r.round_number
            FROM matches m
            JOIN seasons s ON s.id=m.season_id
            JOIN competitions c ON c.id=m.competition_id
            LEFT JOIN rounds r ON r.id=m.round_id
            WHERE c.name='Brasileirão Série A'
              AND s.name='2026'
              AND m.status='finished'
              AND r.round_number >= 15
            ORDER BY r.round_number DESC, m.kickoff_at DESC, m.id DESC
            """
        )
    ).all()
    classifier = MatchContextClassifier(session)
    historical = HistoricalFeatureEngine(session)
    for row in candidates:
        as_of = row.kickoff_at - timedelta(minutes=1)
        classification = classifier.classify(
            row.id,
            as_of=as_of,
            analysis_type=ContextAnalysisType.PRE_MATCH,
        )
        if classification.context is None:
            continue
        features = historical.calculate(match_id=row.id, as_of=as_of)
        if not features.reasons:
            return row, classification.context, features
    raise AssertionError("No real 2026 match had sufficient leakage-safe history")


def main() -> None:
    report: dict[str, object] = {
        "dataset": {},
        "backtest": {},
        "real_chain": {},
        "fail_closed": {},
        "idempotency": {},
    }

    with SessionLocal() as session:
        import_results = {}
        for path in CSV_PATHS:
            result = BrasileiraoCsvImporter(session).import_file(path)
            season = path.name.split("_")[4]
            import_results[season] = {
                "rows_seen": result.rows_seen,
                "matches_created": result.matches_created,
                "matches_updated": result.matches_updated,
                "statistics_upserted": result.statistics_upserted,
            }
            assert result.rows_seen == EXPECTED_ROWS[season], (season, result)

        counts = _season_counts(session)
        for season, expected in EXPECTED_ROWS.items():
            assert counts[season]["matches"] == expected, counts
            assert counts[season]["teams"] == 20, counts
        report["dataset"] = {"imports": import_results, "coverage": counts}

        config = BacktestConfig()
        engine = WalkForwardBacktest(session)
        first = engine.run(config)
        second = engine.run(config)
        assert first.run_id == second.run_id
        assert first.data_fingerprint == second.data_fingerprint
        assert first.metrics.evaluations > 0
        assert first.metrics.probability_evaluations > 0
        assert first.metrics.brier_score is not None
        assert first.metrics.log_loss is not None
        assert first.metrics.baseline_brier_score is not None
        assert first.metrics.baseline_log_loss is not None
        assert first.segments

        first_db_id = persist_backtest_run(session.connection(), first)
        second_db_id = persist_backtest_run(session.connection(), second)
        assert first_db_id == second_db_id
        stored_runs = session.execute(
            text("SELECT count(*) FROM backtest_runs WHERE run_id=:run_id"),
            {"run_id": first.run_id},
        ).scalar_one()
        assert stored_runs == 1
        session.commit()

        report["backtest"] = {
            "run_id": first.run_id,
            "backtest_version": first.backtest_version,
            "data_fingerprint": first.data_fingerprint,
            "evaluations": first.metrics.evaluations,
            "probability_evaluations": first.metrics.probability_evaluations,
            "blocked_evaluations": first.metrics.blocked_evaluations,
            "missing_odd_evaluations": first.metrics.missing_odd_evaluations,
            "bets": first.metrics.bets,
            "brier_score": first.metrics.brier_score,
            "log_loss": first.metrics.log_loss,
            "baseline_brier_score": first.metrics.baseline_brier_score,
            "baseline_log_loss": first.metrics.baseline_log_loss,
            "expected_calibration_error": first.metrics.expected_calibration_error,
            "profit_loss": first.metrics.profit_loss,
            "roi_on_bankroll": first.metrics.roi_on_bankroll,
            "yield_on_stake": first.metrics.yield_on_stake,
            "max_drawdown": first.metrics.max_drawdown,
            "promotion_status": first.metrics.promotion_status.value,
            "segments": len(first.segments),
        }
        report["idempotency"]["backtest_run_single_row"] = True

        row, context, features = _pick_real_chain(session)
        poisson = PoissonModel().calculate(features=features)
        probabilities = MarketProbabilityEngine().calculate_all(poisson=poisson)
        assert len(probabilities) == 6

        chain_rows = []
        journal_ids = []
        go_count = 0
        for probability in probabilities:
            value = ValueEngine().calculate(
                probability=probability,
                market_odd=CONTROLLED_ODD,
                uncertainty_margin_pp=3.0,
                odd_source="QA_CONTROLLED_PRICE_NOT_HISTORICAL",
                odd_observed_at=features.as_of,
            )
            risk = RiskEngine().calculate(
                value=value,
                bankroll_amount=500.0,
                exposure_known=True,
            )
            entry = DecisionJournal().record(
                features=features,
                poisson=poisson,
                probability=probability,
                value=value,
                risk=risk,
                analysis_type=JournalAnalysisType.PRE_MATCH,
                match_type="LEAGUE",
                competition="Brasileirão Série A",
                thesis="MOTOR01_PHASE_ACCEPTANCE",
                evaluated_at=features.as_of,
            )
            first_id = persist_decision_journal_entry(session.connection(), entry)
            second_id = persist_decision_journal_entry(session.connection(), entry)
            assert first_id == second_id
            journal_ids.append(entry.journal_entry_id)
            if value.decision not in (ValueDecision.NO_GO, ValueDecision.OBSERVAR):
                go_count += 1
                assert risk.final_stake_units > 0
                assert risk.stake_amount > 0
            else:
                assert risk.final_stake_units == 0

            chain_rows.append(
                {
                    "market": probability.market.value,
                    "p_model": probability.p_model,
                    "fair_odds": probability.fair_odds,
                    "controlled_odd": CONTROLLED_ODD,
                    "value_decision": value.decision.value,
                    "risk_decision": risk.risk_decision.value,
                    "stake_units": risk.final_stake_units,
                    "stake_amount": risk.stake_amount,
                    "journal_entry_id": entry.journal_entry_id,
                }
            )

        assert go_count >= 1
        session.commit()

        stored_journals = session.execute(
            text(
                """
                SELECT count(*)
                FROM decision_journal_entries
                WHERE journal_entry_id = ANY(:ids)
                """
            ),
            {"ids": journal_ids},
        ).scalar_one()
        assert stored_journals == 6
        report["idempotency"]["decision_journal_single_row_per_market"] = True

        report["real_chain"] = {
            "match_id": row.id,
            "round": row.round_number,
            "kickoff_at": row.kickoff_at.isoformat(),
            "as_of": features.as_of.isoformat(),
            "classifier_version": context.classifier_version,
            "feature_engine_version": features.feature_engine_version,
            "model_version": poisson.model_version,
            "market_count": len(probabilities),
            "go_markets": go_count,
            "controlled_price_note": (
                "Real match/history/model data; goal-market price fixed at 2.50 "
                "only to exercise Value/Risk/Journal because the canonical CSVs "
                "do not contain historical goal-market odds."
            ),
            "markets": chain_rows,
        }

        invalid_context = MatchContextClassifier(session).classify(
            row.id,
            as_of=row.kickoff_at,
            analysis_type=ContextAnalysisType.PRE_MATCH,
        )
        assert invalid_context.context is None
        assert invalid_context.block_reason is not None

        try:
            ValueEngine().calculate(
                probability=probabilities[0],
                market_odd=None,
                uncertainty_margin_pp=3.0,
            )
        except ValueEngineError as exc:
            assert exc.reason is ValueReason.MISSING_MARKET_ODD
            missing_odd_reason = exc.reason.value
        else:
            raise AssertionError("Value Engine must fail closed without market odd")

        report["fail_closed"] = {
            "context_at_or_after_kickoff": invalid_context.block_reason.value,
            "missing_market_odd": missing_odd_reason,
        }

    print("=== MOTOR 01 PHASE ACCEPTANCE ===")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
