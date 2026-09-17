from dataclasses import replace
from datetime import UTC, datetime

import pytest

from apps.api.app.domain.market_probability import Market
from apps.api.app.domain.risk_assessment import OpenPosition, PositionStatus, RiskDecision, RiskReason
from apps.api.app.domain.value_assessment import VALUE_ENGINE_VERSION, ValueAssessment, ValueDecision
from apps.api.app.services.risk_engine import RiskEngine, RiskEngineError

NOW = datetime(2026, 9, 17, 18, tzinfo=UTC)


def value(decision=ValueDecision.GO, market=Market.TOTAL_GOALS_OVER_2_5):
    return ValueAssessment(
        match_id=1532, as_of=NOW, calculated_at=NOW, market=market,
        value_engine_version=VALUE_ENGINE_VERSION, market_engine_version="market-probability-v1",
        model_name="poisson", model_version="poisson-v1", feature_engine_version="feature-engine-v1",
        market_odd=1.8, odd_source="test", odd_observed_at=NOW, p_model=.8,
        uncertainty_margin_pp=3, p_cons=.77, p_break_even=1/1.8, fair_odds=1/.8,
        conservative_fair_odds=1/.77, required_edge_pp=4, edge_pp=21.4444444444,
        ev_cons=.386, odd_min=1.3888888889, confidence=.97, decision=decision, reason=None,
    )


def pos(market, units=1.0, status=PositionStatus.PLACED):
    return OpenPosition("p1", 1532, market, units, status, "w1")


@pytest.mark.parametrize("decision,expected", [
    (ValueDecision.NO_GO, 0), (ValueDecision.OBSERVAR, 0),
    (ValueDecision.GO_CONDICIONAL, .5), (ValueDecision.GO, 1),
    (ValueDecision.GO_FORTE, 1.5), (ValueDecision.GO_PROTEGIDO, 1),
])
def test_base_stakes(decision, expected):
    result = RiskEngine().calculate(value=value(decision), bankroll_amount=500, exposure_known=True)
    assert result.base_stake_units == expected
    assert result.final_stake_units == expected
    assert result.unit_value == 5
    assert result.stake_amount == expected * 5


def test_recommended_and_settled_do_not_consume_exposure():
    positions = (pos(Market.TOTAL_GOALS_OVER_1_5, status=PositionStatus.RECOMMENDED), pos(Market.BTTS_YES, status=PositionStatus.SETTLED))
    result = RiskEngine().calculate(value=value(), bankroll_amount=500, positions=positions, exposure_known=True, wallet_id="w1")
    assert result.current_match_exposure_units == 0
    assert result.final_stake_units == 1


def test_unknown_exposure_preserves_stake_and_warns():
    result = RiskEngine().calculate(value=value(), bankroll_amount=500, exposure_known=False)
    assert result.final_stake_units == 1
    assert result.exposure_warning
    assert result.reason is RiskReason.UNREGISTERED_EXPOSURE_WARNING


@pytest.mark.parametrize("existing,new", [
    (Market.TOTAL_GOALS_OVER_1_5, Market.TOTAL_GOALS_OVER_2_5),
    (Market.TOTAL_GOALS_OVER_1_5, Market.BTTS_YES),
    (Market.TOTAL_GOALS_OVER_2_5, Market.BTTS_YES),
    (Market.TOTAL_GOALS_UNDER_3_5, Market.TOTAL_GOALS_UNDER_4_5),
])
def test_high_correlation_halves_stake(existing, new):
    result = RiskEngine().calculate(value=value(ValueDecision.GO, new), bankroll_amount=500, positions=(pos(existing),), exposure_known=True, wallet_id="w1")
    assert result.final_stake_units == .5
    assert result.risk_decision is RiskDecision.REDUCED_STAKE
    assert result.reason is RiskReason.HIGH_CORRELATION_EXPOSURE


def test_go_forte_correlation_is_point_75_units():
    result = RiskEngine().calculate(value=value(ValueDecision.GO_FORTE), bankroll_amount=500, positions=(pos(Market.TOTAL_GOALS_OVER_1_5),), exposure_known=True, wallet_id="w1")
    assert result.final_stake_units == .75


def test_opposing_btts_is_explicit():
    result = RiskEngine().calculate(value=value(ValueDecision.GO, Market.BTTS_NO), bankroll_amount=500, positions=(pos(Market.BTTS_YES),), exposure_known=True, wallet_id="w1")
    assert result.final_stake_units == .5
    assert result.reason is RiskReason.OPPOSING_MARKET_EXPOSURE


def test_match_limit_reached():
    result = RiskEngine().calculate(value=value(), bankroll_amount=500, positions=(pos(Market.BTTS_YES, 3),), exposure_known=True, wallet_id="w1")
    assert result.final_stake_units == 0
    assert result.reason is RiskReason.MATCH_EXPOSURE_LIMIT_REACHED


def test_capacity_below_minimum_does_not_force_entry():
    result = RiskEngine().calculate(value=value(), bankroll_amount=500, positions=(pos(Market.BTTS_YES, 2.75),), exposure_known=True, wallet_id="w1")
    assert result.final_stake_units == 0
    assert result.reason is RiskReason.INSUFFICIENT_REMAINING_CAPACITY


def test_capacity_caps_stake_when_at_least_minimum():
    result = RiskEngine().calculate(value=value(ValueDecision.GO_FORTE), bankroll_amount=500, positions=(pos(Market.BTTS_NO, 2.25),), exposure_known=True, wallet_id="w1")
    assert result.final_stake_units == .75
    assert result.current_match_exposure_units + result.final_stake_units == 3


@pytest.mark.parametrize("bankroll", [0, -1, float("nan"), float("inf")])
def test_invalid_bankroll_fails(bankroll):
    with pytest.raises(RiskEngineError) as exc:
        RiskEngine().calculate(value=value(), bankroll_amount=bankroll)
    assert exc.value.reason is RiskReason.INVALID_BANKROLL


def test_incompatible_value_version_fails():
    with pytest.raises(RiskEngineError) as exc:
        RiskEngine().calculate(value=replace(value(), value_engine_version="future"), bankroll_amount=500)
    assert exc.value.reason is RiskReason.INCOMPATIBLE_VALUE_ENGINE_VERSION
