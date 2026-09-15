from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from apps.api.app.domain.features import (
    CompetitionBaseline,
    FeatureReason,
    FeatureSet,
    FormWindow,
    StrengthFeatures,
)
from apps.api.app.domain.poisson import PoissonReason
from apps.api.app.services.poisson_model import (
    PoissonModel,
    PoissonModelError,
    poisson_distribution,
    poisson_probability,
)


def make_features(
    *,
    baseline_home: float | None = 1.5,
    baseline_away: float | None = 1.0,
    home_attack: float | None = 1.2,
    home_defence: float | None = 0.8,
    away_attack: float | None = 0.9,
    away_defence: float | None = 1.1,
    reasons: tuple[FeatureReason, ...] = (),
    feature_version: str = "feature-engine-v1",
) -> FeatureSet:
    form = FormWindow(10, 1.0, 1.0, 1.5, 4, 3, 3, True)

    return FeatureSet(
        match_id=123,
        as_of=datetime(2026, 9, 15, 16, tzinfo=timezone.utc),
        calculated_at=datetime(2026, 9, 15, 16, 1, tzinfo=timezone.utc),
        context_classifier_version="match-context-v1",
        feature_engine_version=feature_version,
        home_last5=form,
        home_last10=form,
        away_last5=form,
        away_last10=form,
        home_home5=form,
        home_home10=form,
        away_away5=form,
        away_away10=form,
        competition_baseline=CompetitionBaseline(
            240,
            baseline_home,
            baseline_away,
            None if baseline_home is None or baseline_away is None
            else baseline_home + baseline_away,
        ),
        strengths=StrengthFeatures(
            home_attack,
            home_defence,
            away_attack,
            away_defence,
        ),
        reasons=reasons,
    )


def test_lambda_formulas_and_home_away_baseline_are_correct():
    result = PoissonModel().calculate(features=make_features())

    assert result.lambda_home == pytest.approx(1.5 * 1.2 * 1.1)
    assert result.lambda_away == pytest.approx(1.0 * 0.9 * 0.8)


def test_poisson_zero_probability():
    assert poisson_probability(2.0, 0) == pytest.approx(math.exp(-2.0))


def test_known_poisson_distribution():
    probabilities = poisson_distribution(1.0, 2)

    assert probabilities[0] == pytest.approx(math.exp(-1.0))
    assert probabilities[1] == pytest.approx(math.exp(-1.0))
    assert probabilities[2] == pytest.approx(math.exp(-1.0) / 2)


def test_score_matrix_is_product_of_marginals():
    result = PoissonModel().calculate(features=make_features(), max_goals=3)

    assert result.score_matrix[2][1] == pytest.approx(
        result.home_goal_probabilities[2]
        * result.away_goal_probabilities[1]
    )


def test_matrix_dimensions_and_probability_mass():
    result = PoissonModel().calculate(features=make_features(), max_goals=10)

    assert len(result.score_matrix) == 11
    assert all(len(row) == 11 for row in result.score_matrix)

    direct_sum = math.fsum(math.fsum(row) for row in result.score_matrix)

    assert result.matrix_probability_mass == pytest.approx(direct_sum)
    assert result.tail_probability == pytest.approx(1.0 - direct_sum)
    assert result.tail_probability >= 0


def test_matrix_is_not_silently_renormalized():
    result = PoissonModel().calculate(features=make_features(), max_goals=2)

    assert result.matrix_probability_mass < 1.0
    assert result.tail_probability > 0.0


def test_all_materialized_probabilities_are_finite_and_bounded():
    result = PoissonModel().calculate(features=make_features())

    values = (
        list(result.home_goal_probabilities)
        + list(result.away_goal_probabilities)
        + [cell for row in result.score_matrix for cell in row]
    )

    assert all(math.isfinite(value) for value in values)
    assert all(0.0 <= value <= 1.0 for value in values)


@pytest.mark.parametrize("value", [0.0, -1.0, math.inf, -math.inf, math.nan])
def test_invalid_baseline_is_blocked(value):
    with pytest.raises(PoissonModelError) as exc:
        PoissonModel().calculate(features=make_features(baseline_home=value))

    assert exc.value.reason == PoissonReason.INVALID_BASELINE


@pytest.mark.parametrize("value", [0.0, -1.0, math.inf, -math.inf, math.nan])
def test_invalid_strength_is_blocked(value):
    with pytest.raises(PoissonModelError) as exc:
        PoissonModel().calculate(features=make_features(home_attack=value))

    assert exc.value.reason == PoissonReason.INVALID_STRENGTH


def test_insufficient_features_are_blocked():
    features = make_features(
        reasons=(FeatureReason.INSUFFICIENT_TEAM_HISTORY,)
    )

    with pytest.raises(PoissonModelError) as exc:
        PoissonModel().calculate(features=features)

    assert exc.value.reason == PoissonReason.INSUFFICIENT_FEATURES


def test_incompatible_feature_version_is_blocked():
    with pytest.raises(PoissonModelError) as exc:
        PoissonModel().calculate(
            features=make_features(feature_version="feature-engine-v999")
        )

    assert exc.value.reason == PoissonReason.INCOMPATIBLE_FEATURE_VERSION


def test_same_semantic_input_produces_same_probabilistic_output():
    model = PoissonModel()
    features = make_features()

    first = model.calculate(features=features)
    second = model.calculate(features=features)

    assert first.lambda_home == second.lambda_home
    assert first.lambda_away == second.lambda_away
    assert first.home_goal_probabilities == second.home_goal_probabilities
    assert first.away_goal_probabilities == second.away_goal_probabilities
    assert first.score_matrix == second.score_matrix
    assert first.matrix_probability_mass == second.matrix_probability_mass
    assert first.tail_probability == second.tail_probability
