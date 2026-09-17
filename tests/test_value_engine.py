from __future__ import annotations

from datetime import UTC, datetime

import pytest

from apps.api.app.domain.market_probability import (
    MARKET_ENGINE_VERSION,
    Market,
    MarketProbabilityResult,
)
from apps.api.app.domain.value_assessment import ValueDecision, ValueReason
from apps.api.app.services.value_engine import ValueEngine, ValueEngineError


def probability(p_model: float) -> MarketProbabilityResult:
    return MarketProbabilityResult(
        match_id=1532,
        as_of=datetime(2026, 9, 15, 16, tzinfo=UTC),
        calculated_at=datetime(2026, 9, 15, 16, tzinfo=UTC),
        market=Market.TOTAL_GOALS_UNDER_4_5,
        market_engine_version=MARKET_ENGINE_VERSION,
        model_name="poisson",
        model_version="poisson-v1",
        feature_engine_version="feature-engine-v1",
        p_model=p_model,
        fair_odds=None if p_model == 0.0 else 1.0 / p_model,
    )


@pytest.mark.parametrize(
    ("p_model", "odd", "margin", "decision"),
    [
        (0.69, 2.00, 2.0, ValueDecision.NO_GO),
        (0.72, 2.00, 2.0, ValueDecision.OBSERVAR),
        (0.77, 1.50, 2.0, ValueDecision.GO_CONDICIONAL),
        (0.82, 1.40, 2.0, ValueDecision.GO),
        (0.87, 1.30, 2.0, ValueDecision.GO_FORTE),
        (0.94, 1.12, 2.0, ValueDecision.GO_PROTEGIDO),
    ],
)
def test_decision_bands(p_model: float, odd: float, margin: float, decision: ValueDecision) -> None:
    result = ValueEngine().calculate(
        probability=probability(p_model),
        market_odd=odd,
        uncertainty_margin_pp=margin,
    )
    assert result.decision is decision


def test_zero_probability_is_valid_and_results_in_no_go() -> None:
    result = ValueEngine().calculate(
        probability=probability(0.0), market_odd=2.00, uncertainty_margin_pp=2.0
    )
    assert result.p_model == 0.0
    assert result.p_cons == 0.0
    assert result.fair_odds is None
    assert result.conservative_fair_odds is None
    assert result.decision is ValueDecision.NO_GO
    assert result.reason is ValueReason.PROBABILITY_BELOW_THRESHOLD


def test_calculates_conservative_metrics_without_rounding() -> None:
    result = ValueEngine().calculate(
        probability=probability(0.82), market_odd=1.40, uncertainty_margin_pp=3.0
    )
    assert result.p_cons == pytest.approx(0.79)
    assert result.p_break_even == pytest.approx(1 / 1.40)
    assert result.edge_pp == pytest.approx((0.79 - 1 / 1.40) * 100)
    assert result.ev_cons == pytest.approx(0.79 * 1.40 - 1)
    assert result.confidence == pytest.approx(0.97)
    assert result.required_edge_pp == 4.0
    assert result.odd_min == pytest.approx(1 / (0.79 - 0.04))


def test_high_probability_without_value_is_no_go() -> None:
    result = ValueEngine().calculate(
        probability=probability(0.94), market_odd=1.05, uncertainty_margin_pp=5.0
    )
    assert result.decision is ValueDecision.NO_GO
    assert result.reason is ValueReason.LOW_ODD_REQUIRES_EXCEPTIONAL_EVIDENCE


def test_low_odd_is_not_rejected_when_exceptional_case_has_value() -> None:
    result = ValueEngine().calculate(
        probability=probability(0.94), market_odd=1.12, uncertainty_margin_pp=2.0
    )
    assert result.ev_cons >= 0.03
    assert result.decision is ValueDecision.GO_PROTEGIDO


@pytest.mark.parametrize("odd", [None, 1.0, 0.9, float("nan"), float("inf")])
def test_invalid_market_odd_fails_closed(odd: float | None) -> None:
    with pytest.raises(ValueEngineError) as exc:
        ValueEngine().calculate(
            probability=probability(0.82),
            market_odd=odd,
            uncertainty_margin_pp=3.0,
        )
    expected = ValueReason.MISSING_MARKET_ODD if odd is None else ValueReason.INVALID_MARKET_ODD
    assert exc.value.reason is expected


@pytest.mark.parametrize("margin", [None, -1.0, 0.0, 1.99, 5.01, float("nan")])
def test_invalid_uncertainty_margin_fails_closed(margin: float | None) -> None:
    with pytest.raises(ValueEngineError) as exc:
        ValueEngine().calculate(
            probability=probability(0.82), market_odd=1.40, uncertainty_margin_pp=margin
        )
    assert exc.value.reason is ValueReason.INVALID_UNCERTAINTY_MARGIN


def test_insufficient_edge_does_not_generate_go() -> None:
    result = ValueEngine().calculate(
        probability=probability(0.82), market_odd=1.28, uncertainty_margin_pp=2.0
    )
    assert result.decision is ValueDecision.NO_GO
    assert result.reason is ValueReason.INSUFFICIENT_CONSERVATIVE_EDGE


def test_incompatible_fair_odds_fails_closed() -> None:
    source = probability(0.82)
    invalid = MarketProbabilityResult(
        match_id=source.match_id,
        as_of=source.as_of,
        calculated_at=source.calculated_at,
        market=source.market,
        market_engine_version=source.market_engine_version,
        model_name=source.model_name,
        model_version=source.model_version,
        feature_engine_version=source.feature_engine_version,
        p_model=source.p_model,
        fair_odds=99.0,
    )
    with pytest.raises(ValueEngineError) as exc:
        ValueEngine().calculate(
            probability=invalid, market_odd=1.40, uncertainty_margin_pp=3.0
        )
    assert exc.value.reason is ValueReason.INVALID_MARKET_PROBABILITY
