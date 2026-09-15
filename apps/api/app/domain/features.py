from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


FEATURE_ENGINE_VERSION = "feature-engine-v1"


class FeatureReason(StrEnum):
    INSUFFICIENT_TEAM_HISTORY = "INSUFFICIENT_TEAM_HISTORY"
    INSUFFICIENT_HOME_HISTORY = "INSUFFICIENT_HOME_HISTORY"
    INSUFFICIENT_AWAY_HISTORY = "INSUFFICIENT_AWAY_HISTORY"
    INSUFFICIENT_COMPETITION_BASELINE = "INSUFFICIENT_COMPETITION_BASELINE"
    INVALID_AS_OF = "INVALID_AS_OF"
    INCOMPATIBLE_MATCH_CONTEXT = "INCOMPATIBLE_MATCH_CONTEXT"


@dataclass(frozen=True, slots=True)
class FormWindow:
    games: int
    gf_per_game: float | None
    ga_per_game: float | None
    points_per_game: float | None
    wins: int
    draws: int
    losses: int
    complete: bool


@dataclass(frozen=True, slots=True)
class CompetitionBaseline:
    games: int
    home_goals_per_game: float | None
    away_goals_per_game: float | None
    total_goals_per_game: float | None


@dataclass(frozen=True, slots=True)
class StrengthFeatures:
    home_attack: float | None
    home_defence_conceded: float | None
    away_attack: float | None
    away_defence_conceded: float | None


@dataclass(frozen=True, slots=True)
class FeatureSet:
    match_id: int
    as_of: datetime
    calculated_at: datetime
    context_classifier_version: str
    feature_engine_version: str
    home_last5: FormWindow
    home_last10: FormWindow
    away_last5: FormWindow
    away_last10: FormWindow
    home_home5: FormWindow
    home_home10: FormWindow
    away_away5: FormWindow
    away_away10: FormWindow
    competition_baseline: CompetitionBaseline
    strengths: StrengthFeatures
    reasons: tuple[FeatureReason, ...]
