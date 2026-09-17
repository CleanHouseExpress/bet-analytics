from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from apps.api.app.domain.market_probability import Market
from apps.api.app.domain.value_assessment import ValueDecision

RISK_ENGINE_VERSION = "risk-engine-v1"


class PositionStatus(StrEnum):
    RECOMMENDED = "RECOMMENDED"
    PLACED = "PLACED"
    SETTLED = "SETTLED"
    CANCELLED = "CANCELLED"


class RiskDecision(StrEnum):
    NO_POSITION = "NO_POSITION"
    FULL_STAKE = "FULL_STAKE"
    REDUCED_STAKE = "REDUCED_STAKE"


class RiskReason(StrEnum):
    VALUE_NOT_GO = "VALUE_NOT_GO"
    INVALID_VALUE_ASSESSMENT = "INVALID_VALUE_ASSESSMENT"
    INCOMPATIBLE_VALUE_ENGINE_VERSION = "INCOMPATIBLE_VALUE_ENGINE_VERSION"
    INVALID_BANKROLL = "INVALID_BANKROLL"
    INVALID_UNIT_PERCENT = "INVALID_UNIT_PERCENT"
    INVALID_POSITION = "INVALID_POSITION"
    NO_CONFIRMED_EXPOSURE = "NO_CONFIRMED_EXPOSURE"
    CONFIRMED_EXPOSURE_PRESENT = "CONFIRMED_EXPOSURE_PRESENT"
    HIGH_CORRELATION_EXPOSURE = "HIGH_CORRELATION_EXPOSURE"
    OPPOSING_MARKET_EXPOSURE = "OPPOSING_MARKET_EXPOSURE"
    MATCH_EXPOSURE_LIMIT_REACHED = "MATCH_EXPOSURE_LIMIT_REACHED"
    INSUFFICIENT_REMAINING_CAPACITY = "INSUFFICIENT_REMAINING_CAPACITY"
    UNREGISTERED_EXPOSURE_WARNING = "UNREGISTERED_EXPOSURE_WARNING"


@dataclass(frozen=True, slots=True)
class OpenPosition:
    position_id: str
    match_id: int
    market: Market
    stake_units: float
    status: PositionStatus
    wallet_id: str | None = None


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    match_id: int
    as_of: datetime
    calculated_at: datetime
    market: Market
    risk_engine_version: str
    value_engine_version: str
    market_engine_version: str
    model_version: str
    feature_engine_version: str
    value_decision: ValueDecision
    bankroll_amount: float
    unit_percent: float
    unit_value: float
    base_stake_units: float
    current_match_exposure_units: float
    max_match_exposure_units: float
    remaining_match_capacity_units: float
    correlation_adjustment: float
    final_stake_units: float
    stake_amount: float
    exposure_known: bool
    exposure_warning: str | None
    risk_decision: RiskDecision
    reason: RiskReason
    semantic_hash: str
