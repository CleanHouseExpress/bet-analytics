from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from time import perf_counter

from apps.api.app.domain.features import FEATURE_ENGINE_VERSION, FeatureSet
from apps.api.app.domain.poisson import (
    DEFAULT_MAX_GOALS,
    NUMERICAL_TOLERANCE,
    POISSON_MODEL_NAME,
    POISSON_MODEL_VERSION,
    PoissonReason,
    PoissonResult,
)

logger = logging.getLogger(__name__)


class PoissonModelError(ValueError):
    def __init__(self, reason: PoissonReason):
        super().__init__(reason.value)
        self.reason = reason


def _finite_positive(value: float | None) -> bool:
    return value is not None and math.isfinite(value) and value > 0


def poisson_probability(lambda_: float, k: int) -> float:
    if not _finite_positive(lambda_) or k < 0:
        raise PoissonModelError(PoissonReason.INVALID_LAMBDA)

    log_probability = (
        -lambda_
        + k * math.log(lambda_)
        - math.lgamma(k + 1)
    )
    probability = math.exp(log_probability)

    if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise PoissonModelError(PoissonReason.INVALID_LAMBDA)

    return probability


def poisson_distribution(lambda_: float, max_goals: int) -> tuple[float, ...]:
    if not _finite_positive(lambda_):
        raise PoissonModelError(PoissonReason.INVALID_LAMBDA)

    if max_goals < 0:
        raise ValueError("max_goals must be >= 0")

    return tuple(poisson_probability(lambda_, k) for k in range(max_goals + 1))


class PoissonModel:
    def calculate(
        self,
        *,
        features: FeatureSet,
        max_goals: int = DEFAULT_MAX_GOALS,
    ) -> PoissonResult:
        started_at = perf_counter()

        if features.feature_engine_version != FEATURE_ENGINE_VERSION:
            raise PoissonModelError(PoissonReason.INCOMPATIBLE_FEATURE_VERSION)

        if features.reasons:
            raise PoissonModelError(PoissonReason.INSUFFICIENT_FEATURES)

        if (
            features.as_of.tzinfo is None
            or features.as_of.utcoffset() is None
            or features.match_id <= 0
        ):
            raise PoissonModelError(PoissonReason.INVALID_FEATURE_SET)

        baseline = features.competition_baseline
        strengths = features.strengths

        if not (
            _finite_positive(baseline.home_goals_per_game)
            and _finite_positive(baseline.away_goals_per_game)
        ):
            raise PoissonModelError(PoissonReason.INVALID_BASELINE)

        strength_values = (
            strengths.home_attack,
            strengths.home_defence_conceded,
            strengths.away_attack,
            strengths.away_defence_conceded,
        )
        if not all(_finite_positive(value) for value in strength_values):
            raise PoissonModelError(PoissonReason.INVALID_STRENGTH)

        assert baseline.home_goals_per_game is not None
        assert baseline.away_goals_per_game is not None
        assert strengths.home_attack is not None
        assert strengths.home_defence_conceded is not None
        assert strengths.away_attack is not None
        assert strengths.away_defence_conceded is not None

        lambda_home = (
            baseline.home_goals_per_game
            * strengths.home_attack
            * strengths.away_defence_conceded
        )
        lambda_away = (
            baseline.away_goals_per_game
            * strengths.away_attack
            * strengths.home_defence_conceded
        )

        if not (_finite_positive(lambda_home) and _finite_positive(lambda_away)):
            raise PoissonModelError(PoissonReason.INVALID_LAMBDA)

        if max_goals < 0:
            raise ValueError("max_goals must be >= 0")

        home_probs = poisson_distribution(lambda_home, max_goals)
        away_probs = poisson_distribution(lambda_away, max_goals)

        matrix = tuple(
            tuple(home_probability * away_probability for away_probability in away_probs)
            for home_probability in home_probs
        )

        matrix_mass = math.fsum(math.fsum(row) for row in matrix)
        tail_probability = 1.0 - matrix_mass

        if tail_probability < 0 and abs(tail_probability) <= NUMERICAL_TOLERANCE:
            tail_probability = 0.0

        result = PoissonResult(
            match_id=features.match_id,
            as_of=features.as_of.astimezone(timezone.utc),
            calculated_at=datetime.now(timezone.utc),
            model_name=POISSON_MODEL_NAME,
            model_version=POISSON_MODEL_VERSION,
            feature_engine_version=features.feature_engine_version,
            lambda_home=lambda_home,
            lambda_away=lambda_away,
            max_goals=max_goals,
            home_goal_probabilities=home_probs,
            away_goal_probabilities=away_probs,
            score_matrix=matrix,
            matrix_probability_mass=matrix_mass,
            tail_probability=tail_probability,
        )

        logger.info(
            "poisson_model_calculated",
            extra={
                "match_id": result.match_id,
                "as_of": result.as_of.isoformat(),
                "model_name": result.model_name,
                "model_version": result.model_version,
                "feature_engine_version": result.feature_engine_version,
                "lambda_home": result.lambda_home,
                "lambda_away": result.lambda_away,
                "max_goals": result.max_goals,
                "matrix_probability_mass": result.matrix_probability_mass,
                "tail_probability": result.tail_probability,
                "duration_ms": round((perf_counter() - started_at) * 1000, 3),
            },
        )

        return result
