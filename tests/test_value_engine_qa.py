from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from apps.api.app.domain.market_probability import (
    MARKET_ENGINE_VERSION,
    Market,
    MarketProbabilityResult,
)
from apps.api.app.domain.value_assessment import ValueDecision, ValueReason
from apps.api.app.services.value_assessment_snapshot import semantic_hash
from apps.api.app.services.value_engine import ValueEngine, ValueEngineError


def probability(p_model: float = 0.82) -> MarketProbabilityResult:
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
    ("p_model", "margin", "required_edge_pp"),
    [
        (0.77, 2.0, 5.0),
        (0.82, 3.0, 4.0),
        (0.87, 2.0, 3.0),
    ],
)
def test_odd_min_matches_required_edge_by_probability_band(
    p_model: float, margin: float, required_edge_pp: float
) -> None:
    result = ValueEngine().calculate(
        probability=probability(p_model),
        market_odd=2.0,
        uncertainty_margin_pp=margin,
    )
    p_cons = p_model - margin / 100.0
    expected = max(1.0 / (p_cons - required_edge_pp / 100.0), 1.0 / p_cons)
    assert result.required_edge_pp == required_edge_pp
    assert result.odd_min == pytest.approx(expected)


def test_protected_odd_min_requires_three_percent_conservative_ev() -> None:
    result = ValueEngine().calculate(
        probability=probability(0.94), market_odd=1.20, uncertainty_margin_pp=2.0
    )
    assert result.odd_min == pytest.approx(1.03 / 0.92)
    assert result.ev_cons >= 0.03
    assert result.decision is ValueDecision.GO_PROTEGIDO


def test_exact_break_even_ev_is_not_go() -> None:
    p_model = 0.82
    margin = 2.0
    p_cons = p_model - margin / 100.0
    result = ValueEngine().calculate(
        probability=probability(p_model),
        market_odd=1.0 / p_cons,
        uncertainty_margin_pp=margin,
    )
    assert result.ev_cons == pytest.approx(0.0, abs=1e-12)
    assert result.decision is ValueDecision.NO_GO
    assert result.reason is ValueReason.NON_POSITIVE_CONSERVATIVE_EV


def test_odd_below_130_is_not_automatically_rejected() -> None:
    result = ValueEngine().calculate(
        probability=probability(0.89), market_odd=1.25, uncertainty_margin_pp=2.0
    )
    assert result.market_odd < 1.30
    assert result.edge_pp >= 3.0
    assert result.ev_cons > 0.0
    assert result.decision is ValueDecision.GO_FORTE


def test_incompatible_bets5_engine_version_fails_closed() -> None:
    source = replace(probability(), market_engine_version="market-probability-v2")
    with pytest.raises(ValueEngineError) as exc:
        ValueEngine().calculate(
            probability=source, market_odd=1.40, uncertainty_margin_pp=3.0
        )
    assert exc.value.reason is ValueReason.INCOMPATIBLE_MARKET_ENGINE_VERSION


@pytest.mark.parametrize("p_model", [-0.01, 1.01, float("nan"), float("inf")])
def test_invalid_probability_fails_closed(p_model: float) -> None:
    source = replace(probability(), p_model=p_model, fair_odds=2.0)
    with pytest.raises(ValueEngineError) as exc:
        ValueEngine().calculate(
            probability=source, market_odd=1.40, uncertainty_margin_pp=3.0
        )
    assert exc.value.reason is ValueReason.INVALID_MARKET_PROBABILITY


def test_same_semantic_input_has_same_metrics_and_hash() -> None:
    engine = ValueEngine()
    first = engine.calculate(
        probability=probability(0.87),
        market_odd=1.30,
        uncertainty_margin_pp=2.0,
        odd_source="qa-fixture",
        odd_observed_at=datetime(2026, 9, 15, 15, 55, tzinfo=UTC),
    )
    second = engine.calculate(
        probability=probability(0.87),
        market_odd=1.30,
        uncertainty_margin_pp=2.0,
        odd_source="qa-fixture",
        odd_observed_at=datetime(2026, 9, 15, 15, 55, tzinfo=UTC),
    )
    assert first.p_cons == second.p_cons
    assert first.p_break_even == second.p_break_even
    assert first.edge_pp == second.edge_pp
    assert first.ev_cons == second.ev_cons
    assert first.odd_min == second.odd_min
    assert first.decision is second.decision
    assert first.reason is second.reason
    assert semantic_hash(first) == semantic_hash(second)


def test_success_and_no_go_logs_are_auditable(caplog) -> None:
    engine = ValueEngine()
    with caplog.at_level("INFO"):
        result = engine.calculate(
            probability=probability(0.82), market_odd=1.40, uncertainty_margin_pp=3.0
        )
    success = next(
        record for record in caplog.records if record.message == "value_assessment_calculated"
    )
    assert success.match_id == result.match_id
    assert success.market == result.market.value
    assert success.market_engine_version == MARKET_ENGINE_VERSION
    assert success.p_model == result.p_model
    assert success.p_cons == result.p_cons
    assert success.edge_pp == result.edge_pp
    assert success.ev_cons == result.ev_cons
    assert success.decision == result.decision.value

    caplog.clear()
    with caplog.at_level("INFO"):
        no_go = engine.calculate(
            probability=probability(0.69), market_odd=2.0, uncertainty_margin_pp=2.0
        )
    logged = next(
        record for record in caplog.records if record.message == "value_assessment_calculated"
    )
    assert no_go.decision is ValueDecision.NO_GO
    assert logged.decision == ValueDecision.NO_GO.value
    assert logged.reason == ValueReason.PROBABILITY_BELOW_THRESHOLD.value


def test_blocked_log_contains_explicit_reason(caplog) -> None:
    engine = ValueEngine()
    with caplog.at_level("WARNING"), pytest.raises(ValueEngineError):
        engine.calculate(
            probability=probability(0.82), market_odd=None, uncertainty_margin_pp=3.0
        )
    blocked = next(
        record for record in caplog.records if record.message == "value_assessment_blocked"
    )
    assert blocked.match_id == 1532
    assert blocked.reason == ValueReason.MISSING_MARKET_ODD.value
    assert blocked.value_engine_version == "value-engine-v1"
