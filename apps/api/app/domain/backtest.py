from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from apps.api.app.domain.decision_journal import DecisionJournalEntry, SettlementResult
from apps.api.app.domain.market_probability import Market
from apps.api.app.domain.risk_assessment import RiskDecision
from apps.api.app.domain.value_assessment import ValueDecision

BACKTEST_VERSION = "walk-forward-motor-01-v1"
DEFAULT_BACKTEST_SEASONS = ("2024", "2025", "2026")
DEFAULT_MIN_SAMPLE_FOR_REVIEW = 100


class BacktestEvaluationStatus(StrEnum):
    EVALUATED = "EVALUATED"
    MISSING_ODD = "MISSING_ODD"
    BLOCKED = "BLOCKED"


class BacktestPromotionStatus(StrEnum):
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
    NOT_BETTER_THAN_BASELINE = "NOT_BETTER_THAN_BASELINE"
    NEGATIVE_FINANCIAL_PERFORMANCE = "NEGATIVE_FINANCIAL_PERFORMANCE"
    ELIGIBLE_FOR_REVIEW = "ELIGIBLE_FOR_REVIEW"


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    competition_name: str = "Brasileirão Série A"
    seasons: tuple[str, ...] = DEFAULT_BACKTEST_SEASONS
    markets: tuple[Market, ...] = tuple(Market)
    initial_bankroll: float = 500.0
    uncertainty_margin_pp: float = 3.0
    as_of_offset_seconds: int = 60
    min_sample_for_review: int = DEFAULT_MIN_SAMPLE_FOR_REVIEW
    thesis: str = "BETS-9-WALK-FORWARD-MOTOR-01"


@dataclass(frozen=True, slots=True)
class CalibrationBucket:
    label: str
    lower_bound: float
    upper_bound: float
    count: int
    mean_probability: float | None
    observed_rate: float | None
    absolute_gap: float | None


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    evaluations: int
    probability_evaluations: int
    blocked_evaluations: int
    missing_odd_evaluations: int
    no_go_decisions: int
    observe_decisions: int
    go_decisions: int
    bets: int
    wins: int
    losses: int
    accuracy: float | None
    brier_score: float | None
    log_loss: float | None
    baseline_brier_score: float | None
    baseline_log_loss: float | None
    expected_calibration_error: float | None
    stake_total: float
    profit_loss: float
    roi_on_bankroll: float
    yield_on_stake: float | None
    max_drawdown: float
    initial_bankroll: float
    ending_bankroll: float
    promotion_status: BacktestPromotionStatus
    calibration: tuple[CalibrationBucket, ...]


@dataclass(frozen=True, slots=True)
class BacktestSegment:
    dimension: str
    value: str
    evaluations: int
    bets: int
    hit_rate: float | None
    brier_score: float | None
    baseline_brier_score: float | None
    stake_total: float
    profit_loss: float
    yield_on_stake: float | None


@dataclass(frozen=True, slots=True)
class BacktestEvaluation:
    match_id: int
    season: str
    round_number: int | None
    kickoff_at: datetime
    as_of: datetime
    market: Market
    status: BacktestEvaluationStatus
    p_model: float | None
    baseline_probability: float | None
    actual_outcome: bool
    model_side: str | None
    market_odd: float | None
    odd_source: str | None
    odd_observed_at: datetime | None
    value_decision: ValueDecision | None
    risk_decision: RiskDecision | None
    stake_units: float
    stake_value: float
    settlement_result: SettlementResult | None
    settled_at: datetime | None
    profit_loss: float | None
    journal_entry: DecisionJournalEntry | None
    feature_semantic_hash: str | None
    poisson_semantic_hash: str | None
    market_probability_semantic_hash: str | None
    value_semantic_hash: str | None
    risk_semantic_hash: str | None
    feature_payload: str | None
    block_reason: str | None


@dataclass(frozen=True, slots=True)
class BacktestRun:
    run_id: str
    semantic_hash: str
    data_fingerprint: str
    generated_at: datetime
    backtest_version: str
    config: BacktestConfig
    evaluations: tuple[BacktestEvaluation, ...]
    metrics: BacktestMetrics
    segments: tuple[BacktestSegment, ...]
    equity_curve: tuple[float, ...]
