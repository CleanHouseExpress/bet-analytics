from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from apps.api.app.core.database import SessionLocal
from apps.api.app.domain.history import Competition, Match, Season, Team
from apps.api.app.domain.market_probability import Market
from apps.api.app.domain.poisson import POISSON_MODEL_NAME, POISSON_MODEL_VERSION, PoissonResult
from apps.api.app.services.market_probability import MarketProbabilityEngine
from apps.api.app.services.market_probability_snapshot import (
    MarketProbabilitySnapshotConflict,
    evaluation_key,
    persist_market_probability_snapshot,
    semantic_hash,
)


MATCH_ID = 9_910_001
COMPETITION_ID = 9_910_001
SEASON_ID = 9_910_001
HOME_TEAM_ID = 9_910_001
AWAY_TEAM_ID = 9_910_002
AS_OF = datetime(2026, 9, 15, 16, tzinfo=UTC)


def poisson_result() -> PoissonResult:
    lh = 1.3658536585365852
    la = 0.6593406593406593
    hp = tuple(math.exp(-lh) * lh**k / math.factorial(k) for k in range(11))
    ap = tuple(math.exp(-la) * la**k / math.factorial(k) for k in range(11))
    matrix = tuple(tuple(h * a for a in ap) for h in hp)
    mass = math.fsum(math.fsum(row) for row in matrix)
    return PoissonResult(
        match_id=MATCH_ID,
        as_of=AS_OF,
        calculated_at=datetime(2026, 9, 15, 16, 1, tzinfo=UTC),
        model_name=POISSON_MODEL_NAME,
        model_version=POISSON_MODEL_VERSION,
        feature_engine_version="feature-engine-v1",
        lambda_home=lh,
        lambda_away=la,
        max_goals=10,
        home_goal_probabilities=hp,
        away_goal_probabilities=ap,
        score_matrix=matrix,
        matrix_probability_mass=mass,
        tail_probability=1.0 - mass,
    )


def result():
    return MarketProbabilityEngine().calculate(
        poisson=poisson_result(), market=Market.TOTAL_GOALS_OVER_2_5
    )


def _seed_match(session) -> None:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    session.add(
        Competition(
            id=COMPETITION_ID,
            name="BETS snapshot competition",
            country_code="BRA",
            competition_type="league",
            created_at=now,
            updated_at=now,
        )
    )
    session.add_all(
        [
            Team(
                id=HOME_TEAM_ID,
                name="BETS snapshot home",
                country_code="BRA",
                created_at=now,
                updated_at=now,
            ),
            Team(
                id=AWAY_TEAM_ID,
                name="BETS snapshot away",
                country_code="BRA",
                created_at=now,
                updated_at=now,
            ),
        ]
    )
    session.add(
        Season(
            id=SEASON_ID,
            competition_id=COMPETITION_ID,
            name="2026 BETS snapshot",
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


def test_evaluation_key_is_deterministic():
    first = result()
    second = replace(first, calculated_at=datetime(2030, 1, 1, tzinfo=UTC))
    assert evaluation_key(first) == evaluation_key(second)
    assert semantic_hash(first) == semantic_hash(second)


def test_semantic_change_changes_hash():
    first = result()
    changed = replace(first, p_model=first.p_model / 2, fair_odds=first.fair_odds * 2)
    assert semantic_hash(first) != semantic_hash(changed)


def test_persistence_is_idempotent_and_conflict_is_explicit():
    session = SessionLocal()
    try:
        _seed_match(session)
        first = result()
        row1 = persist_market_probability_snapshot(session, result=first)
        row2 = persist_market_probability_snapshot(
            session,
            result=replace(first, calculated_at=datetime(2030, 1, 1, tzinfo=UTC)),
        )
        assert row1["evaluation_key"] == row2["evaluation_key"]
        assert row1["semantic_hash"] == row2["semantic_hash"]

        changed = replace(
            first,
            p_model=first.p_model / 2,
            fair_odds=first.fair_odds * 2,
        )
        with pytest.raises(MarketProbabilitySnapshotConflict):
            persist_market_probability_snapshot(session, result=changed)
    finally:
        session.rollback()
        session.close()
