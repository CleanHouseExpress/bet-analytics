from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from enum import Enum

from sqlalchemy import text
from sqlalchemy.engine import Connection

from apps.api.app.domain.backtest import BacktestEvaluation, BacktestRun
from apps.api.app.domain.decision_journal import DECISION_JOURNAL_VERSION
from apps.api.app.domain.features import FEATURE_ENGINE_VERSION
from apps.api.app.domain.market_probability import MARKET_ENGINE_VERSION
from apps.api.app.domain.poisson import POISSON_MODEL_NAME, POISSON_MODEL_VERSION
from apps.api.app.domain.risk_assessment import RISK_ENGINE_VERSION
from apps.api.app.domain.value_assessment import VALUE_ENGINE_VERSION


class BacktestSnapshotConflictError(RuntimeError):
    pass


def _jsonable(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _payload(value: object) -> dict[str, object]:
    return _jsonable(asdict(value))


def _stable_run_payload(run: BacktestRun) -> dict[str, object]:
    payload = _payload(run)
    payload.pop("generated_at", None)
    return payload


def _stable_evaluation_payload(
    evaluation: BacktestEvaluation,
) -> dict[str, object]:
    payload = _payload(evaluation)
    journal = payload.get("journal_entry")
    if isinstance(journal, dict):
        journal.pop("evaluated_at", None)
    return payload


def persist_backtest_run(conn: Connection, run: BacktestRun) -> int:
    if run.run_id != run.semantic_hash:
        raise BacktestSnapshotConflictError("BACKTEST_RUN_IDENTITY_MISMATCH")

    payload = _payload(run)
    stable_payload = _stable_run_payload(run)
    metrics = _jsonable(asdict(run.metrics))
    segments = _jsonable([asdict(item) for item in run.segments])
    config = _jsonable(asdict(run.config))

    sql = """
        INSERT INTO backtest_runs (
            run_id, semantic_hash, data_fingerprint, backtest_version,
            competition_name, seasons, config, feature_engine_version,
            model_name, model_version, market_engine_version,
            value_engine_version, risk_engine_version,
            decision_journal_version, initial_bankroll, ending_bankroll,
            metrics, segments, equity_curve, payload, generated_at
        ) VALUES (
            :run_id, :semantic_hash, :data_fingerprint, :backtest_version,
            :competition_name, :seasons, :config, :feature_engine_version,
            :model_name, :model_version, :market_engine_version,
            :value_engine_version, :risk_engine_version,
            :decision_journal_version, :initial_bankroll, :ending_bankroll,
            :metrics, :segments, :equity_curve, :payload, :generated_at
        )
    """
    if conn.dialect.name == "postgresql":
        for parameter in (
            "seasons",
            "config",
            "metrics",
            "segments",
            "equity_curve",
            "payload",
        ):
            sql = sql.replace(f":{parameter}", f"CAST(:{parameter} AS JSONB)")
        sql += " ON CONFLICT (run_id) DO NOTHING RETURNING id"
    else:
        sql = sql.replace(
            "INSERT INTO backtest_runs",
            "INSERT OR IGNORE INTO backtest_runs",
            1,
        )
        sql += " RETURNING id"

    row = conn.execute(
        text(sql),
        {
            "run_id": run.run_id,
            "semantic_hash": run.semantic_hash,
            "data_fingerprint": run.data_fingerprint,
            "backtest_version": run.backtest_version,
            "competition_name": run.config.competition_name,
            "seasons": json.dumps(list(run.config.seasons), sort_keys=True),
            "config": json.dumps(config, sort_keys=True, allow_nan=False),
            "feature_engine_version": FEATURE_ENGINE_VERSION,
            "model_name": POISSON_MODEL_NAME,
            "model_version": POISSON_MODEL_VERSION,
            "market_engine_version": MARKET_ENGINE_VERSION,
            "value_engine_version": VALUE_ENGINE_VERSION,
            "risk_engine_version": RISK_ENGINE_VERSION,
            "decision_journal_version": DECISION_JOURNAL_VERSION,
            "initial_bankroll": run.metrics.initial_bankroll,
            "ending_bankroll": run.metrics.ending_bankroll,
            "metrics": json.dumps(metrics, sort_keys=True, allow_nan=False),
            "segments": json.dumps(segments, sort_keys=True, allow_nan=False),
            "equity_curve": json.dumps(
                list(run.equity_curve),
                sort_keys=True,
                allow_nan=False,
            ),
            "payload": json.dumps(payload, sort_keys=True, allow_nan=False),
            "generated_at": run.generated_at,
        },
    ).scalar_one_or_none()

    if row is None:
        existing = conn.execute(
            text("SELECT id, payload FROM backtest_runs WHERE run_id=:run_id"),
            {"run_id": run.run_id},
        ).mappings().one()
        existing_payload = existing["payload"]
        if isinstance(existing_payload, str):
            existing_payload = json.loads(existing_payload)
        existing_payload = dict(existing_payload)
        existing_payload.pop("generated_at", None)
        if existing_payload != stable_payload:
            raise BacktestSnapshotConflictError("BACKTEST_RUN_SEMANTIC_CONFLICT")
        run_db_id = int(existing["id"])
    else:
        run_db_id = int(row)

    for evaluation in run.evaluations:
        _persist_evaluation(conn, run.run_id, evaluation)
    return run_db_id


def _persist_evaluation(
    conn: Connection,
    run_id: str,
    evaluation: BacktestEvaluation,
) -> int:
    payload = _stable_evaluation_payload(evaluation)
    feature_payload = (
        json.loads(evaluation.feature_payload)
        if evaluation.feature_payload is not None
        else None
    )
    journal_entry_id = (
        evaluation.journal_entry.journal_entry_id
        if evaluation.journal_entry is not None
        else None
    )
    sql = """
        INSERT INTO backtest_evaluations (
            run_id, match_id, season, round_number, kickoff_at, as_of,
            market, status, p_model, p_cons, p_break_even, edge_pp, ev_cons,
            confidence, baseline_probability, actual_outcome, model_side,
            market_odd, odd_source, odd_observed_at,
            value_decision, risk_decision, stake_units, stake_value,
            settlement_result, settled_at, profit_loss, journal_entry_id,
            feature_semantic_hash, poisson_semantic_hash,
            market_probability_semantic_hash, value_semantic_hash,
            risk_semantic_hash, feature_payload, block_reason, payload
        ) VALUES (
            :run_id, :match_id, :season, :round_number, :kickoff_at, :as_of,
            :market, :status, :p_model, :p_cons, :p_break_even, :edge_pp,
            :ev_cons, :confidence, :baseline_probability, :actual_outcome,
            :model_side, :market_odd, :odd_source, :odd_observed_at,
            :value_decision, :risk_decision, :stake_units, :stake_value,
            :settlement_result, :settled_at, :profit_loss, :journal_entry_id,
            :feature_semantic_hash, :poisson_semantic_hash,
            :market_probability_semantic_hash, :value_semantic_hash,
            :risk_semantic_hash, :feature_payload, :block_reason, :payload
        )
    """
    if conn.dialect.name == "postgresql":
        sql = sql.replace(":feature_payload", "CAST(:feature_payload AS JSONB)")
        sql = sql.replace(":payload", "CAST(:payload AS JSONB)")
        sql += (
            " ON CONFLICT (run_id, match_id, market) "
            "DO NOTHING RETURNING id"
        )
    else:
        sql = sql.replace(
            "INSERT INTO backtest_evaluations",
            "INSERT OR IGNORE INTO backtest_evaluations",
            1,
        )
        sql += " RETURNING id"

    row = conn.execute(
        text(sql),
        {
            "run_id": run_id,
            "match_id": evaluation.match_id,
            "season": evaluation.season,
            "round_number": evaluation.round_number,
            "kickoff_at": evaluation.kickoff_at,
            "as_of": evaluation.as_of,
            "market": evaluation.market.value,
            "status": evaluation.status.value,
            "p_model": evaluation.p_model,
            "p_cons": evaluation.p_cons,
            "p_break_even": evaluation.p_break_even,
            "edge_pp": evaluation.edge_pp,
            "ev_cons": evaluation.ev_cons,
            "confidence": evaluation.confidence,
            "baseline_probability": evaluation.baseline_probability,
            "actual_outcome": evaluation.actual_outcome,
            "model_side": evaluation.model_side,
            "market_odd": evaluation.market_odd,
            "odd_source": evaluation.odd_source,
            "odd_observed_at": evaluation.odd_observed_at,
            "value_decision": (
                evaluation.value_decision.value
                if evaluation.value_decision is not None
                else None
            ),
            "risk_decision": (
                evaluation.risk_decision.value
                if evaluation.risk_decision is not None
                else None
            ),
            "stake_units": evaluation.stake_units,
            "stake_value": evaluation.stake_value,
            "settlement_result": (
                evaluation.settlement_result.value
                if evaluation.settlement_result is not None
                else None
            ),
            "settled_at": evaluation.settled_at,
            "profit_loss": evaluation.profit_loss,
            "journal_entry_id": journal_entry_id,
            "feature_semantic_hash": evaluation.feature_semantic_hash,
            "poisson_semantic_hash": evaluation.poisson_semantic_hash,
            "market_probability_semantic_hash": (
                evaluation.market_probability_semantic_hash
            ),
            "value_semantic_hash": evaluation.value_semantic_hash,
            "risk_semantic_hash": evaluation.risk_semantic_hash,
            "feature_payload": (
                json.dumps(feature_payload, sort_keys=True, allow_nan=False)
                if feature_payload is not None
                else None
            ),
            "block_reason": evaluation.block_reason,
            "payload": json.dumps(payload, sort_keys=True, allow_nan=False),
        },
    ).scalar_one_or_none()
    if row is not None:
        return int(row)

    existing = conn.execute(
        text(
            """
            SELECT id, payload
            FROM backtest_evaluations
            WHERE run_id=:run_id AND match_id=:match_id AND market=:market
            """
        ),
        {
            "run_id": run_id,
            "match_id": evaluation.match_id,
            "market": evaluation.market.value,
        },
    ).mappings().one()
    existing_payload = existing["payload"]
    if isinstance(existing_payload, str):
        existing_payload = json.loads(existing_payload)
    if dict(existing_payload) != payload:
        raise BacktestSnapshotConflictError(
            "BACKTEST_EVALUATION_SEMANTIC_CONFLICT"
        )
    return int(existing["id"])
