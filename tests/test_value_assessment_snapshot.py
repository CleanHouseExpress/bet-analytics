from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from apps.api.app.core.database import SessionLocal
from apps.api.app.domain.history import Competition, Match, Season, Team
from apps.api.app.domain.market_probability import (
    MARKET_ENGINE_VERSION,
    Market,
    MarketProbabilityResult,
)
from apps.api.app.services.value_assessment_snapshot import (
    ValueAssessmentSnapshotConflict,
    evaluation_key,
    persist_value_assessment_snapshot,
    semantic_hash,
)
from apps.api.app.services.value_engine import ValueEngine

MATCH_ID = 9_920_001
COMPETITION_ID = 9_920_001
SEASON_ID = 9_920_001
HOME_TEAM_ID = 9_920_001
AWAY_TEAM_ID = 9_920_002
AS_OF = datetime(2026, 9, 15, 16, tzinfo=UTC)


def probability() -> MarketProbabilityResult:
    p_model = 0.82
    return MarketProbabilityResult(
        match_id=MATCH_ID,
        as_of=AS_OF,
        calculated_at=AS_OF,
        market=Market.TOTAL_GOALS_UNDER_4_5,
        market_engine_version=MARKET_ENGINE_VERSION,
        model_name="poisson",
        model_version="poisson-v1",
        feature_engine_version="feature-engine-v1",
        p_model=p_model,
        fair_odds=1.0 / p_model,
    )


def result():
    return ValueEngine().calculate(
        probability=probability(), market_odd=1.40, uncertainty_margin_pp=3.0
    )


def _seed_match(session) -> None:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    session.add(
        Competition(
            id=COMPETITION_ID,
            name="BETS value snapshot competition",
            country_code="BRA",
            competition_type="league",
            created_at=now,
            updated_at=now,
        )
    )
    session.add_all(
        [
            Team(id=HOME_TEAM_ID, name="BETS value home", country_code="BRA", created_at=now, updated_at=now),
            Team(id=AWAY_TEAM_ID, name="BETS value away", country_code="BRA", created_at=now, updated_at=now),
        ]
    )
    session.add(
        Season(
            id=SEASON_ID,
            competition_id=COMPETITION_ID,
            name="2026 BETS value snapshot",
            is_current=True,
            created_at=now,
            updated_at=now,
        )
    )
    session.add(
        Match(
            id=MATCH_ID,
            competition_id=COMPETITION_ID,
            season_id=SEASON_ID,
            home_team_id=HOME_TEAM_ID,
            away_team_id=AWAY_TEAM_ID,
            kickoff_at=AS_OF + timedelta(days=1),
            status="scheduled",
            created_at=now,
            updated_at=now,
        )
    )
    session.flush()


def test_value_snapshot_key_and_hash_ignore_calculated_at():
    first = result()
    second = replace(first, calculated_at=datetime(2030, 1, 1, tzinfo=UTC))
    assert evaluation_key(first) == evaluation_key(second)
    assert semantic_hash(first) == semantic_hash(second)


def test_value_snapshot_semantic_change_changes_hash():
    first = result()
    changed = replace(first, edge_pp=first.edge_pp + 1.0)
    assert evaluation_key(first) == evaluation_key(changed)
    assert semantic_hash(first) != semantic_hash(changed)


def test_value_snapshot_persistence_is_idempotent_and_conflict_is_explicit():
    session = SessionLocal()
    try:
        _seed_match(session)
        first = result()
        row1 = persist_value_assessment_snapshot(session, result=first)
        row2 = persist_value_assessment_snapshot(
            session,
            result=replace(first, calculated_at=datetime(2030, 1, 1, tzinfo=UTC)),
        )
        assert row1["evaluation_key"] == row2["evaluation_key"]
        assert row1["semantic_hash"] == row2["semantic_hash"]

        changed = replace(first, edge_pp=first.edge_pp + 1.0)
        with pytest.raises(ValueAssessmentSnapshotConflict):
            persist_value_assessment_snapshot(session, result=changed)
    finally:
        session.rollback()
        session.close()
