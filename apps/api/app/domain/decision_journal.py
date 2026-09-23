from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from apps.api.app.domain.market_probability import Market
from apps.api.app.domain.risk_assessment import RiskDecision
from apps.api.app.domain.value_assessment import ValueDecision

DECISION_JOURNAL_VERSION = "decision-journal-v1"


class AnalysisType(StrEnum):
    PRE_MATCH = "PRE_MATCH"
    LIVE = "LIVE"


class SettlementResult(StrEnum):
    GREEN = "GREEN"
    RED = "RED"
    VOID = "VOID"


@dataclass(frozen=True, slots=True)
class DecisionJournalEntry:
    journal_entry_id: str
    match_id: int
    as_of: datetime
    evaluated_at: datetime
    market: Market
    analysis_type: AnalysisType
    decision_journal_version: str
    semantic_hash: str
    feature_engine_version: str
    model_name: str
    model_version: str
    market_engine_version: str
    value_engine_version: str
    risk_engine_version: str
    feature_semantic_hash: str
    poisson_semantic_hash: str
    market_probability_semantic_hash: str
    value_semantic_hash: str
    risk_semantic_hash: str
    competition: str | None
    match_type: str
    thesis: str | None
    p_model: float
    p_cons: float
    p_break_even: float
    fair_odds: float | None
    market_odd: float
    odd_min: float | None
    edge_pp: float
    ev_cons: float
    confidence: float
    value_decision: ValueDecision
    risk_decision: RiskDecision
    stake_units: float
    stake_value: float
    exposure_known: bool
    reason: str


@dataclass(frozen=True, slots=True)
class DecisionSettlement:
    journal_entry_id: str
    result: SettlementResult
    profit_loss: float
    closing_odd: float | None
    clv: float | None
    settled_at: datetime
