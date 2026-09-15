from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


POISSON_MODEL_NAME = "poisson"
POISSON_MODEL_VERSION = "poisson-v1"
DEFAULT_MAX_GOALS = 10
NUMERICAL_TOLERANCE = 1e-12


class PoissonReason(StrEnum):
    INVALID_FEATURE_SET = "INVALID_FEATURE_SET"
    INSUFFICIENT_FEATURES = "INSUFFICIENT_FEATURES"
    INVALID_BASELINE = "INVALID_BASELINE"
    INVALID_STRENGTH = "INVALID_STRENGTH"
    INVALID_LAMBDA = "INVALID_LAMBDA"
    INCOMPATIBLE_FEATURE_VERSION = "INCOMPATIBLE_FEATURE_VERSION"


@dataclass(frozen=True, slots=True)
class PoissonResult:
    match_id: int
    as_of: datetime
    calculated_at: datetime
    model_name: str
    model_version: str
    feature_engine_version: str
    lambda_home: float
    lambda_away: float
    max_goals: int
    home_goal_probabilities: tuple[float, ...]
    away_goal_probabilities: tuple[float, ...]
    score_matrix: tuple[tuple[float, ...], ...]
    matrix_probability_mass: float
    tail_probability: float
