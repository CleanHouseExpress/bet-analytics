from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from apps.api.app.domain.decision_journal import DecisionJournalEntry, SettlementResult
from apps.api.app.domain.market_probability import Market
from apps.api.app.domain.risk_assessment import RiskDecision
from apps.api.app.domain.value_assessment import ValueDecision

BACKTEST_VERSION = "walk-forward-motor-01-v2"
DEFAULT_BACKTEST_SEASONS = ("2024", "2025", "2026")
DEFAULT_MIN_SAMPLE_FOR_REVIEW = 100


@dataclass(frozen=True, slots=True)
class SeasonCoverageManifest:
    season: str
    minimum_matches: int
    expected_teams: int
    minimum_matches_by_round: tuple[tuple[int, int], ...]
    source: str
    allow_additional_rounds: bool = False


_FULL_SERIE_A_ROUNDS = tuple((round_number, 10) for round_number in range(1, 39))
DEFAULT_BACKTEST_COVERAGE_MANIFEST = (
    SeasonCoverageManifest(
        season="2024",
        minimum_matches=380,
        expected_teams=20,
        minimum_matches_by_round=_FULL_SERIE_A_ROUNDS,
        source="docs/brasileirao_serie_a_2024_enriched_v2.csv",
    ),
    SeasonCoverageManifest(
        season="2025",
        minimum_matches=380,
        expected_teams=20,
        minimum_matches_by_round=_FULL_SERIE_A_ROUNDS,
        source="docs/brasileirao_serie_a_2025_enriched_v6.csv",
    ),
    SeasonCoverageManifest(
        season="2026",
        minimum_matches=215,
        expected_teams=20,
        minimum_matches_by_round=(
            (1, 10),
            (2, 10),
            (3, 10),
            (4, 9),
            (5, 10),
            (6, 10),
            (7, 10),
            (8, 10),
            (9, 10),
            (10, 10),
            (11, 10),
            (12, 10),
            (13, 10),
            (14, 10),
            (15, 10),
            (16, 10),
            (17, 10),
            (18, 10),
            (19, 10),
            (20, 10),
            (21, 6),
            (22, 10),
        ),
        source="docs/brasileirao_serie_a_2026_enriched_v5.csv",
        allow_additional_rounds=True,
    ),
)


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
    coverage_manifest: tuple[SeasonCoverageManifest, ...] = (
        DEFAULT_BACKTEST_COVERAGE_MANIFEST
    )


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
    p_cons: float | None
    p_break_even: float | None
    edge_pp: float | None
    ev_cons: float | None
    confidence: float | None
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
