from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from apps.api.app.core.database import SessionLocal
from apps.api.app.domain.match_context import (
    AnalysisType,
    CompetitionFormat,
    MatchContext,
)
from apps.api.app.services.feature_engine import FeatureEngine
from apps.api.app.services.poisson_model import PoissonModel
from apps.api.app.services.poisson_snapshot import semantic_hash


MATCH_ID = 1532
AS_OF = datetime(2026, 9, 15, 16, 0, tzinfo=UTC)


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


def test_real_bets3_features_feed_poisson_v1():
    with SessionLocal() as session:
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

        # Valores derivados independentemente da referência QA da BETS-3.
        assert result.lambda_home == pytest.approx(
            1.3658536585365852
        )
        assert result.lambda_away == pytest.approx(
            0.6593406593406593
        )

        assert result.max_goals == 10
        assert len(result.home_goal_probabilities) == 11
        assert len(result.away_goal_probabilities) == 11
        assert len(result.score_matrix) == 11
        assert all(
            len(row) == 11
            for row in result.score_matrix
        )

        assert 0.0 < result.matrix_probability_mass <= 1.0
        assert result.tail_probability == pytest.approx(
            1.0 - result.matrix_probability_mass
        )


def test_reused_feature_set_is_independent_of_database_changes():
    with SessionLocal() as session:
        features = _features(session)

        before = PoissonModel().calculate(features=features)
        before_hash = semantic_hash(before)

        # Cria uma alteração real no banco APÓS o FeatureSet existir.
        # O Poisson não pode consultar esse dado nem mudar seu resultado.
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

        session.rollback()


def test_matrix_is_product_of_bets3_derived_marginals():
    with SessionLocal() as session:
        result = PoissonModel().calculate(
            features=_features(session)
        )

        for home_goals in range(result.max_goals + 1):
            for away_goals in range(result.max_goals + 1):
                expected = (
                    result.home_goal_probabilities[home_goals]
                    * result.away_goal_probabilities[away_goals]
                )

                assert (
                    result.score_matrix[home_goals][away_goals]
                    == pytest.approx(expected)
                )
