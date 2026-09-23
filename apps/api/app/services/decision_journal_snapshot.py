from __future__ import annotations

import json
import logging
from dataclasses import asdict

from sqlalchemy import text
from sqlalchemy.engine import Connection

from apps.api.app.domain.decision_journal import DecisionJournalEntry

logger = logging.getLogger(__name__)


class DecisionJournalConflictError(RuntimeError):
    pass


def _jsonable(value: object) -> object:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return value


def _payload(entry: DecisionJournalEntry) -> dict[str, object]:
    return {key: _jsonable(value) for key, value in asdict(entry).items()}


def persist_decision_journal_entry(
    conn: Connection,
    entry: DecisionJournalEntry,
) -> int:
    payload = _payload(entry)
    existing = conn.execute(
        text(
            "SELECT id, payload FROM decision_journal_entries "
            "WHERE semantic_hash=:hash"
        ),
        {"hash": entry.semantic_hash},
    ).mappings().first()
    if existing:
        old_payload = existing["payload"]
        if isinstance(old_payload, str):
            old_payload = json.loads(old_payload)
        old_payload = dict(old_payload)
        old_payload.pop("evaluated_at", None)
        current = dict(payload)
        current.pop("evaluated_at", None)
        if old_payload != current:
            logger.warning(
                "decision_journal_blocked",
                extra={
                    "journal_hash": entry.semantic_hash,
                    "match_id": entry.match_id,
                    "market": entry.market.value,
                    "reason": "DECISION_JOURNAL_SEMANTIC_CONFLICT",
                },
            )
            raise DecisionJournalConflictError(
                "DECISION_JOURNAL_SEMANTIC_CONFLICT"
            )
        return int(existing["id"])

    sql = """
        INSERT INTO decision_journal_entries (
            journal_entry_id, semantic_hash, match_id, as_of, evaluated_at,
            market, analysis_type, decision_journal_version,
            feature_engine_version, model_name, model_version,
            market_engine_version, value_engine_version, risk_engine_version,
            feature_semantic_hash, poisson_semantic_hash,
            market_probability_semantic_hash, value_semantic_hash,
            risk_semantic_hash, value_decision, risk_decision, stake_units,
            stake_value, exposure_known, payload
        ) VALUES (
            :journal_entry_id, :semantic_hash, :match_id, :as_of, :evaluated_at,
            :market, :analysis_type, :journal_version, :feature_version,
            :model_name, :model_version, :market_version, :value_version,
            :risk_version, :feature_hash, :poisson_hash, :probability_hash,
            :value_hash, :risk_hash, :value_decision, :risk_decision,
            :stake_units, :stake_value, :exposure_known, :payload
        ) RETURNING id
    """
    if conn.dialect.name == "postgresql":
        sql = sql.replace(":payload", "CAST(:payload AS JSONB)")
    row = conn.execute(
        text(sql),
        {
            "journal_entry_id": entry.journal_entry_id,
            "semantic_hash": entry.semantic_hash,
            "match_id": entry.match_id,
            "as_of": entry.as_of,
            "evaluated_at": entry.evaluated_at,
            "market": entry.market.value,
            "analysis_type": entry.analysis_type.value,
            "journal_version": entry.decision_journal_version,
            "feature_version": entry.feature_engine_version,
            "model_name": entry.model_name,
            "model_version": entry.model_version,
            "market_version": entry.market_engine_version,
            "value_version": entry.value_engine_version,
            "risk_version": entry.risk_engine_version,
            "feature_hash": entry.feature_semantic_hash,
            "poisson_hash": entry.poisson_semantic_hash,
            "probability_hash": entry.market_probability_semantic_hash,
            "value_hash": entry.value_semantic_hash,
            "risk_hash": entry.risk_semantic_hash,
            "value_decision": entry.value_decision.value,
            "risk_decision": entry.risk_decision.value,
            "stake_units": entry.stake_units,
            "stake_value": entry.stake_value,
            "exposure_known": entry.exposure_known,
            "payload": json.dumps(payload, sort_keys=True, allow_nan=False),
        },
    ).scalar_one()
    logger.info(
        "decision_journal_recorded",
        extra={
            "journal_hash": entry.semantic_hash,
            "match_id": entry.match_id,
            "market": entry.market.value,
            "value_decision": entry.value_decision.value,
            "risk_decision": entry.risk_decision.value,
            "stake_units": entry.stake_units,
            "reason": entry.reason,
        },
    )
    return int(row)
