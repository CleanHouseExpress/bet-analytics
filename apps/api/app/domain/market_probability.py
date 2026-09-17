from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

MARKET_ENGINE_VERSION = "market-probability-v1"


class Market(StrEnum):
    TOTAL_GOALS_OVER_1_5 = "TOTAL_GOALS_OVER_1_5"
    TOTAL_GOALS_OVER_2_5 = "TOTAL_GOALS_OVER_2_5"
    TOTAL_GOALS_UNDER_3_5 = "TOTAL_GOALS_UNDER_3_5"
    TOTAL_GOALS_UNDER_4_5 = "TOTAL_GOALS_UNDER_4_5"
    BTTS_YES = "BTTS_YES"
    BTTS_NO = "BTTS_NO"


class MarketProbabilityReason(StrEnum):
    INVALID_POISSON_RESULT = "INVALID_POISSON_RESULT"
    INCOMPATIBLE_MODEL_VERSION = "INCOMPATIBLE_MODEL_VERSION"
    INVALID_LAMBDA = "INVALID_LAMBDA"
    INVALID_PROBABILITY_MASS = "INVALID_PROBABILITY_MASS"
    INVALID_MARKET_PROBABILITY = "INVALID_MARKET_PROBABILITY"
    UNSUPPORTED_MARKET = "UNSUPPORTED_MARKET"


@dataclass(frozen=True, slots=True)
class MarketProbabilityResult:
    match_id: int
    as_of: datetime
    calculated_at: datetime
    market: Market
    market_engine_version: str
    model_name: str
    model_version: str
    feature_engine_version: str
    p_model: float
    fair_odds: float | None
