from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime
from enum import Enum

from sqlalchemy import text
from sqlalchemy.orm import Session

from apps.api.app.domain.features import FeatureSet


class FeatureSnapshotConflict(ValueError):
    pass


def _jsonable(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def semantic_payload(features: FeatureSet) -> dict[str, object]:
    payload = _jsonable(asdict(features))
    payload.pop("calculated_at", None)
    return payload


def semantic_hash(features: FeatureSet) -> str:
    encoded = json.dumps(semantic_payload(features), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def persist_feature_snapshot(
    session: Session,
    *,
    evaluation_key: str,
    features: FeatureSet,
) -> dict[str, object]:
    """Atomically persist one immutable semantic snapshot per evaluation key."""
    if not evaluation_key.strip():
        raise ValueError("evaluation_key is required")

    payload = semantic_payload(features)
    digest = semantic_hash(features)
    params = {
        "evaluation_key": evaluation_key,
        "match_id": features.match_id,
        "as_of": features.as_of,
        "feature_engine_version": features.feature_engine_version,
        "context_classifier_version": features.context_classifier_version,
        "payload": json.dumps(payload, sort_keys=True),
        "semantic_hash": digest,
        "calculated_at": features.calculated_at,
    }

    row = session.execute(
        text(
            """
            INSERT INTO feature_snapshots (
                evaluation_key, match_id, as_of, feature_engine_version,
                context_classifier_version, payload, semantic_hash, calculated_at
            ) VALUES (
                :evaluation_key, :match_id, :as_of, :feature_engine_version,
                :context_classifier_version, CAST(:payload AS jsonb), :semantic_hash,
                :calculated_at
            )
            ON CONFLICT (evaluation_key) DO NOTHING
            RETURNING evaluation_key, match_id, as_of, feature_engine_version,
                      context_classifier_version, payload, semantic_hash, calculated_at
            """
        ),
        params,
    ).mappings().one_or_none()

    if row is None:
        row = session.execute(
            text(
                """
                SELECT evaluation_key, match_id, as_of, feature_engine_version,
                       context_classifier_version, payload, semantic_hash, calculated_at
                  FROM feature_snapshots
                 WHERE evaluation_key = :evaluation_key
                """
            ),
            {"evaluation_key": evaluation_key},
        ).mappings().one()
        if row["semantic_hash"] != digest:
            raise FeatureSnapshotConflict(
                "evaluation_key already has a different immutable feature snapshot"
            )

    session.flush()
    return dict(row)
