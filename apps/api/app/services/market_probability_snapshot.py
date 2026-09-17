from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from apps.api.app.domain.market_probability import MarketProbabilityResult


class MarketProbabilitySnapshotConflict(ValueError):
    pass


def _jsonable(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def semantic_payload(result: MarketProbabilityResult) -> dict[str, object]:
    payload = _jsonable(asdict(result))
    payload.pop("calculated_at", None)
    return payload


def semantic_hash(result: MarketProbabilityResult) -> str:
    encoded = json.dumps(
        semantic_payload(result),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def evaluation_key(result: MarketProbabilityResult) -> str:
    return ":".join(
        (
            str(result.match_id),
            result.as_of.isoformat(),
            result.market.value,
            result.market_engine_version,
            result.model_version,
            result.feature_engine_version,
        )
    )


def persist_market_probability_snapshot(
    session: Session,
    *,
    result: MarketProbabilityResult,
) -> dict[str, object]:
    key = evaluation_key(result)
    payload = semantic_payload(result)
    digest = semantic_hash(result)
    params = {
        "evaluation_key": key,
        "match_id": result.match_id,
        "as_of": result.as_of,
        "market": result.market.value,
        "market_engine_version": result.market_engine_version,
        "model_name": result.model_name,
        "model_version": result.model_version,
        "feature_engine_version": result.feature_engine_version,
        "payload": json.dumps(payload, sort_keys=True, allow_nan=False),
        "semantic_hash": digest,
        "calculated_at": result.calculated_at,
    }
    row = session.execute(
        text("""
            INSERT INTO market_probability_snapshots (
                evaluation_key, match_id, as_of, market, market_engine_version,
                model_name, model_version, feature_engine_version, payload,
                semantic_hash, calculated_at
            ) VALUES (
                :evaluation_key, :match_id, :as_of, :market, :market_engine_version,
                :model_name, :model_version, :feature_engine_version,
                CAST(:payload AS jsonb), :semantic_hash, :calculated_at
            )
            ON CONFLICT (evaluation_key) DO NOTHING
            RETURNING *
        """),
        params,
    ).mappings().one_or_none()
    if row is None:
        row = session.execute(
            text("SELECT * FROM market_probability_snapshots WHERE evaluation_key = :evaluation_key"),
            {"evaluation_key": key},
        ).mappings().one()
        if row["semantic_hash"] != digest:
            raise MarketProbabilitySnapshotConflict(
                "evaluation_key already has a different immutable market probability snapshot"
            )
    session.flush()
    return dict(row)
