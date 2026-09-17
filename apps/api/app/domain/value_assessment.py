from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from apps.api.app.domain.market_probability import Market

VALUE_ENGINE_VERSION = "value-engine-v1"


class ValueDecision(StrEnum):
    NO_GO = "NO_GO"
    OBSERVAR = "OBSERVAR"
    GO_CONDICIONAL = "GO_CONDICIONAL"
    GO = "GO"
    GO_FORTE = "GO_FORTE"
    GO_PROTEGIDO = "GO_PROTEGIDO"


class ValueReason(StrEnum):
    PROBABILITY_BELOW_THRESHOLD = "PROBABILITY_BELOW_THRESHOLD"
    OBSERVATION_BAND = "OBSERVATION_BAND"
    MISSING_MARKET_ODD = "MISSING_MARKET_ODD"
    INVALID_MARKET_ODD = "INVALID_MARKET_ODD"
    INVALID_MARKET_PROBABILITY = "INVALID_MARKET_PROBABILITY"
    INVALID_UNCERTAINTY_MARGIN = "INVALID_UNCERTAINTY_MARGIN"
    INSUFFICIENT_CONSERVATIVE_EDGE = "INSUFFICIENT_CONSERVATIVE_EDGE"
    NON_POSITIVE_CONSERVATIVE_EV = "NON_POSITIVE_CONSERVATIVE_EV"
    LOW_ODD_REQUIRES_EXCEPTIONAL_EVIDENCE = (
        "LOW_ODD_REQUIRES_EXCEPTIONAL_EVIDENCE"
    )
    INCOMPATIBLE_MARKET_ENGINE_VERSION = "INCOMPATIBLE_MARKET_ENGINE_VERSION"


@dataclass(frozen=True, slots=True)
class ValueAssessment:
    match_id: int
    as_of: datetime
    calculated_at: datetime
    market: Market
    value_engine_version: str
    market_engine_version: str
    model_name: str
    model_version: str
    feature_engine_version: str
    market_odd: float
    odd_source: str | None
    odd_observed_at: datetime | None
    p_model: float
    uncertainty_margin_pp: float
    p_cons: float
    p_break_even: float
    fair_odds: float | None
    conservative_fair_odds: float | None
    required_edge_pp: float | None
    edge_pp: float
    ev_cons: float
    odd_min: float | None
    confidence: float
    decision: ValueDecision
    reason: ValueReason | None
