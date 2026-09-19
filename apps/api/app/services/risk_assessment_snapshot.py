from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from sqlalchemy import text
from sqlalchemy.engine import Connection

from apps.api.app.domain.risk_assessment import (
    OpenPosition,
    PositionStatus,
    RiskAssessment,
)


class RiskAssessmentConflictError(RuntimeError):
    pass


def _jsonable(value: object) -> object:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return value


def _canonical_positions(
    positions: tuple[OpenPosition, ...],
) -> list[dict[str, object]]:
    placed = [
        {
            "position_id": position.position_id,
            "match_id": position.match_id,
            "market": position.market.value,
            "stake_units": position.stake_units,
            "status": position.status.value,
            "wallet_id": position.wallet_id,
        }
        for position in positions
        if position.status is PositionStatus.PLACED
    ]
    return sorted(
        placed,
        key=lambda item: (
            str(item["position_id"]),
            str(item["market"]),
            format(float(item["stake_units"]), ".17g"),
            str(item["wallet_id"] or ""),
        ),
    )


def _exposure_hash(positions: list[dict[str, object]]) -> str:
    semantic = [
        {
            "position_id": item["position_id"],
            "market": item["market"],
            "stake_units": format(float(item["stake_units"]), ".17g"),
            "status": item["status"],
            "wallet_id": item["wallet_id"],
        }
        for item in positions
    ]
    payload = json.dumps(
        {"positions": semantic},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def persist_risk_assessment(
    conn: Connection,
    assessment: RiskAssessment,
    positions: tuple[OpenPosition, ...],
) -> int:
    payload = {key: _jsonable(value) for key, value in asdict(assessment).items()}
    canonical_positions = _canonical_positions(positions)
    if _exposure_hash(canonical_positions) != assessment.exposure_semantic_hash:
        raise RiskAssessmentConflictError("RISK_ASSESSMENT_EXPOSURE_MISMATCH")

    existing = conn.execute(
        text(
            "SELECT id, assessment_json, positions_json "
            "FROM risk_assessment_snapshots WHERE semantic_hash=:hash"
        ),
        {"hash": assessment.semantic_hash},
    ).mappings().first()
    if existing:
        old_assessment = existing["assessment_json"]
        old_positions = existing["positions_json"]
        if isinstance(old_assessment, str):
            old_assessment = json.loads(old_assessment)
        if isinstance(old_positions, str):
            old_positions = json.loads(old_positions)
        old_assessment = dict(old_assessment)
        old_assessment.pop("calculated_at", None)
        current = dict(payload)
        current.pop("calculated_at", None)
        if old_assessment != current or old_positions != canonical_positions:
            raise RiskAssessmentConflictError(
                "RISK_ASSESSMENT_SEMANTIC_CONFLICT"
            )
        return int(existing["id"])

    sql = """
        INSERT INTO risk_assessment_snapshots (
            semantic_hash, match_id, as_of, market, risk_engine_version,
            value_engine_version, market_engine_version, model_version,
            feature_engine_version, value_semantic_hash, exposure_semantic_hash,
            bankroll_amount, unit_percent, exposure_known, positions_json,
            assessment_json, calculated_at
        ) VALUES (
            :hash, :match_id, :as_of, :market, :risk, :value, :market_engine,
            :model, :feature, :value_hash, :exposure_hash, :bankroll, :unit,
            :known, :positions, :assessment, :calculated_at
        ) RETURNING id
    """
    if conn.dialect.name == "postgresql":
        sql = sql.replace(":positions", "CAST(:positions AS JSON)")
        sql = sql.replace(":assessment", "CAST(:assessment AS JSON)")

    row = conn.execute(
        text(sql),
        {
            "hash": assessment.semantic_hash,
            "match_id": assessment.match_id,
            "as_of": assessment.as_of,
            "market": assessment.market.value,
            "risk": assessment.risk_engine_version,
            "value": assessment.value_engine_version,
            "market_engine": assessment.market_engine_version,
            "model": assessment.model_version,
            "feature": assessment.feature_engine_version,
            "value_hash": assessment.value_semantic_hash,
            "exposure_hash": assessment.exposure_semantic_hash,
            "bankroll": assessment.bankroll_amount,
            "unit": assessment.unit_percent,
            "known": assessment.exposure_known,
            "positions": json.dumps(canonical_positions, sort_keys=True),
            "assessment": json.dumps(payload, sort_keys=True),
            "calculated_at": assessment.calculated_at,
        },
    ).scalar_one()
    return int(row)
