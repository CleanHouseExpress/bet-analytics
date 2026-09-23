import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text

from apps.api.app.core.database import engine as postgres_engine
from apps.api.app.domain.market_probability import Market
from apps.api.app.domain.risk_assessment import OpenPosition, PositionStatus
from apps.api.app.domain.value_assessment import (
    VALUE_ENGINE_VERSION,
    ValueAssessment,
    ValueDecision,
)
from apps.api.app.services.risk_assessment_snapshot import (
    RiskAssessmentConflictError,
    persist_risk_assessment,
)
from apps.api.app.services.risk_engine import RiskEngine

NOW = datetime(2026, 9, 17, 18, tzinfo=UTC)


def _value():
    return ValueAssessment(
        1532,
        NOW,
        NOW,
        Market.TOTAL_GOALS_OVER_2_5,
        VALUE_ENGINE_VERSION,
        "market-probability-v1",
        "poisson",
        "poisson-v1",
        "feature-engine-v1",
        1.8,
        "test",
        NOW,
        0.8,
        3,
        0.77,
        1 / 1.8,
        1 / 0.8,
        1 / 0.77,
        4,
        21.44,
        0.386,
        1.38,
        0.97,
        ValueDecision.GO,
        None,
    )


def _setup():
    engine = create_engine("sqlite://")
    conn = engine.connect()
    conn.execute(
        text(
            "CREATE TABLE risk_assessment_snapshots ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "semantic_hash TEXT UNIQUE NOT NULL, "
            "match_id INTEGER NOT NULL, as_of DATETIME NOT NULL, "
            "market TEXT NOT NULL, risk_engine_version TEXT NOT NULL, "
            "value_engine_version TEXT NOT NULL, "
            "market_engine_version TEXT NOT NULL, "
            "model_version TEXT NOT NULL, feature_engine_version TEXT NOT NULL, "
            "value_semantic_hash TEXT NOT NULL, "
            "exposure_semantic_hash TEXT NOT NULL, "
            "bankroll_amount FLOAT NOT NULL, unit_percent FLOAT NOT NULL, "
            "exposure_known BOOLEAN NOT NULL, positions_json JSON NOT NULL, "
            "assessment_json JSON NOT NULL, calculated_at DATETIME NOT NULL)"
        )
    )
    return engine, conn


def test_snapshot_idempotent():
    engine, conn = _setup()
    positions = (
        OpenPosition(
            "p1",
            1532,
            Market.BTTS_YES,
            0.5,
            PositionStatus.PLACED,
            "w1",
        ),
    )
    assessment = RiskEngine().calculate(
        value=_value(),
        bankroll_amount=500,
        positions=positions,
        exposure_known=True,
        wallet_id="w1",
    )
    first = persist_risk_assessment(conn, assessment, positions)
    second = persist_risk_assessment(conn, assessment, positions)
    assert first == second
    assert (
        conn.execute(
            text("SELECT count(*) FROM risk_assessment_snapshots")
        ).scalar_one()
        == 1
    )
    conn.close()
    engine.dispose()


def test_same_hash_divergent_payload_conflicts():
    engine, conn = _setup()
    assessment = RiskEngine().calculate(
        value=_value(),
        bankroll_amount=500,
        exposure_known=True,
    )
    persist_risk_assessment(conn, assessment, ())
    row = conn.execute(
        text(
            "SELECT assessment_json FROM risk_assessment_snapshots "
            "WHERE semantic_hash=:hash"
        ),
        {"hash": assessment.semantic_hash},
    ).scalar_one()
    payload = json.loads(row) if isinstance(row, str) else dict(row)
    payload["stake_amount"] = 999
    conn.execute(
        text(
            "UPDATE risk_assessment_snapshots SET assessment_json=:payload "
            "WHERE semantic_hash=:hash"
        ),
        {"payload": json.dumps(payload), "hash": assessment.semantic_hash},
    )
    with pytest.raises(RiskAssessmentConflictError):
        persist_risk_assessment(conn, assessment, ())
    conn.close()
    engine.dispose()


def test_persist_rejects_placed_position_from_other_match():
    engine, conn = _setup()
    positions = (
        OpenPosition(
            "p1",
            1532,
            Market.BTTS_YES,
            0.5,
            PositionStatus.PLACED,
            "w1",
        ),
    )
    assessment = RiskEngine().calculate(
        value=_value(),
        bankroll_amount=500,
        positions=positions,
        exposure_known=True,
        wallet_id="w1",
    )
    wrong_match = (replace(positions[0], match_id=999),)
    with pytest.raises(
        RiskAssessmentConflictError,
        match="RISK_ASSESSMENT_EXPOSURE_MATCH_MISMATCH",
    ):
        persist_risk_assessment(conn, assessment, wrong_match)
    conn.close()
    engine.dispose()


def test_postgres_concurrent_snapshot_is_idempotent():
    assessment = RiskEngine().calculate(
        value=_value(),
        bankroll_amount=500,
        exposure_known=True,
    )
    with postgres_engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM risk_assessment_snapshots "
                "WHERE semantic_hash=:hash"
            ),
            {"hash": assessment.semantic_hash},
        )

    def persist_once():
        with postgres_engine.begin() as conn:
            return persist_risk_assessment(conn, assessment, ())

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: persist_once(), range(2)))

    try:
        assert ids[0] == ids[1]
        with postgres_engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT count(*) FROM risk_assessment_snapshots "
                    "WHERE semantic_hash=:hash"
                ),
                {"hash": assessment.semantic_hash},
            ).scalar_one()
        assert count == 1
    finally:
        with postgres_engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM risk_assessment_snapshots "
                    "WHERE semantic_hash=:hash"
                ),
                {"hash": assessment.semantic_hash},
            )


def test_persist_rejects_positions_different_from_calculation():
    engine, conn = _setup()
    assessment = RiskEngine().calculate(
        value=_value(),
        bankroll_amount=500,
        exposure_known=True,
    )
    other_positions = (
        OpenPosition(
            "unexpected",
            1532,
            Market.BTTS_YES,
            0.5,
            PositionStatus.PLACED,
            "w1",
        ),
    )
    with pytest.raises(
        RiskAssessmentConflictError,
        match="RISK_ASSESSMENT_EXPOSURE_MISMATCH",
    ):
        persist_risk_assessment(conn, assessment, other_positions)
    assert (
        conn.execute(
            text("SELECT count(*) FROM risk_assessment_snapshots")
        ).scalar_one()
        == 0
    )
    conn.close()
    engine.dispose()
