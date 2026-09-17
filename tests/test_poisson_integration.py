from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from apps.api.app.core.database import SessionLocal
from apps.api.app.domain.history import Competition, Match, Season, Team
from apps.api.app.domain.market_probability import Market
from apps.api.app.domain.match_context import (
    AnalysisType,
    CompetitionFormat,
    MatchContext,
)
from apps.api.app.services.feature_engine import FeatureEngine
from apps.api.app.services.market_probability import MarketProbabilityEngine
from apps.api.app.services.poisson_model import PoissonModel
from apps.api.app.services.poisson_snapshot import semantic_hash


MATCH_ID = 9_900_001
COMPETITION_ID = 9_900_001
SEASON_ID = 9_900_001
HOME_TEAM_ID = 9_900_001
AWAY_TEAM_ID = 9_900_002
HOME_OPPONENT_ID = 9_900_003
AWAY_OPPONENT_ID = 9_900_004
AS_OF = datetime(2026, 9, 15, 16, 0, tzinfo=UTC)


def _seed_fixture(session) -> None:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    session.add(
        Competition(
            id=COMPETITION_ID,
            name="BETS test competition",
            country_code="BRA",
            competition_type="league",
            created_at=now,
            updated_at=now,
        )
    )
    session.add_all(
        [
            Team(
                id=team_id,
                name=name,
                country_code="BRA",
                created_at=now,
                updated_at=now,
            )
            for team_id, name in (
                (HOME_TEAM_ID, "BETS home"),
                (AWAY_TEAM_ID, "BETS away"),
                (HOME_OPPONENT_ID, "BETS home opponent"),
                (AWAY_OPPONENT_ID, "BETS away opponent"),
            )
        ]
    )
    session.add(
        Season(
            id=SEASON_ID,
            competition_id=COMPETITION_ID,
            name="2026 BETS fixture",
            is_current=True,
            created_at=now,
            updated_at=now,
        )
    )

    for index in range(10):
        kickoff = AS_OF - timedelta(days=30 - index)
        session.add(
            Match(
                id=9_901_000 + index,
                competition_id=COMPETITION_ID,
                season_id=SEASON_ID,
                home_team_id=HOME_TEAM_ID,
                away_team_id=HOME_OPPONENT_ID,
                kickoff_at=kickoff,
                status="finished",
                home_score=2,
                away_score=1,
                finished_at=kickoff + timedelta(hours=2),
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            Match(
                id=9_902_000 + index,
                competition_id=COMPETITION_ID,
                season_id=SEASON_ID,
                home_team_id=AWAY_OPPONENT_ID,
                away_team_id=AWAY_TEAM_ID,
                kickoff_at=kickoff + timedelta(hours=3),
                status="finished",
                home_score=1,
                away_score=1,
                finished_at=kickoff + timedelta(hours=5),
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


@pytest.fixture
def session():
    db = SessionLocal()
    try:
        _seed_fixture(db)
        yield db
    finally:
        db.rollback()
        db.close()


def _load_context(session) -> MatchContext:
    row = session.execute(
        text(
            """
            SELECT id, competition_id, season_id
            FROM matches
            WHERE id = :match_id
            """
        ),
        {"match_id": MATCH_ID},
    ).mappings().one()

    return MatchContext(
        match_id=row["id"],
        competition_id=row["competition_id"],
        season_id=row["season_id"],
        competition_format=CompetitionFormat.LEAGUE_POINTS,
        analysis_type=AnalysisType.PRE_MATCH,
        classified_at=AS_OF,
        as_of=AS_OF,
    )


def _features(session):
    context = _load_context(session)
    return FeatureEngine(session).calculate(
        match_id=MATCH_ID,
        as_of=AS_OF,
        context=context,
    )


def test_bets3_features_feed_poisson_v1(session):
    features = _features(session)
    assert features.reasons == ()

    result = PoissonModel().calculate(features=features)
    expected_home = (
        features.competition_baseline.home_goals_per_game
        * features.strengths.home_attack
        * features.strengths.away_defence_conceded
    )
    expected_away = (
        features.competition_baseline.away_goals_per_game
        * features.strengths.away_attack
        * features.strengths.home_defence_conceded
    )

    assert result.match_id == MATCH_ID
    assert result.as_of == AS_OF
    assert result.model_name == "poisson"
    assert result.model_version == "poisson-v1"
    assert result.feature_engine_version == "feature-engine-v1"
    assert result.lambda_home == pytest.approx(expected_home)
    assert result.lambda_away == pytest.approx(expected_away)
    assert result.lambda_home == pytest.approx(4 / 3)
    assert result.lambda_away == pytest.approx(1.0)
    assert result.max_goals == 10
    assert len(result.home_goal_probabilities) == 11
    assert len(result.away_goal_probabilities) == 11
    assert len(result.score_matrix) == 11
    assert all(len(row) == 11 for row in result.score_matrix)
    assert 0.0 < result.matrix_probability_mass <= 1.0
    assert result.tail_probability == pytest.approx(
        1.0 - result.matrix_probability_mass
    )


def test_bets3_bets4_feed_all_bets5_markets(session):
    features = _features(session)
    poisson = PoissonModel().calculate(features=features)
    results = MarketProbabilityEngine().calculate_all(poisson=poisson)
    by_market = {result.market: result for result in results}

    assert len(results) == 6
    assert set(by_market) == set(Market)

    lambda_total = poisson.lambda_home + poisson.lambda_away

    def cdf(maximum: int) -> float:
        return math.fsum(
            math.exp(-lambda_total)
            * lambda_total**goals
            / math.factorial(goals)
            for goals in range(maximum + 1)
        )

    expected = {
        Market.TOTAL_GOALS_OVER_1_5: 1.0 - cdf(1),
        Market.TOTAL_GOALS_OVER_2_5: 1.0 - cdf(2),
        Market.TOTAL_GOALS_UNDER_3_5: cdf(3),
        Market.TOTAL_GOALS_UNDER_4_5: cdf(4),
        Market.BTTS_YES: (
            1.0
            - math.exp(-poisson.lambda_home)
            - math.exp(-poisson.lambda_away)
            + math.exp(-lambda_total)
        ),
    }
    expected[Market.BTTS_NO] = 1.0 - expected[Market.BTTS_YES]

    for market, probability in expected.items():
        result = by_market[market]
        assert result.match_id == poisson.match_id
        assert result.as_of == poisson.as_of
        assert result.model_version == poisson.model_version
        assert result.feature_engine_version == poisson.feature_engine_version
        assert result.p_model == pytest.approx(probability, abs=1e-12)
        assert result.fair_odds == pytest.approx(1.0 / probability, abs=1e-12)

    assert by_market[Market.BTTS_YES].p_model + by_market[Market.BTTS_NO].p_model == pytest.approx(
        1.0, abs=1e-12
    )


def test_reused_feature_set_is_independent_of_database_changes(session):
    features = _features(session)
    before = PoissonModel().calculate(features=features)
    before_hash = semantic_hash(before)

    session.execute(
        text(
            """
            UPDATE matches
            SET updated_at = updated_at + interval '1 second'
            WHERE id = :match_id
            """
        ),
        {"match_id": MATCH_ID},
    )

    after = PoissonModel().calculate(features=features)
    assert semantic_hash(after) == before_hash


def test_matrix_is_product_of_bets3_derived_marginals(session):
    result = PoissonModel().calculate(features=_features(session))

    for home_goals in range(result.max_goals + 1):
        for away_goals in range(result.max_goals + 1):
            expected = (
                result.home_goal_probabilities[home_goals]
                * result.away_goal_probabilities[away_goals]
            )
            assert result.score_matrix[home_goals][away_goals] == pytest.approx(
                expected
            )
