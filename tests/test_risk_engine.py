from dataclasses import replace
from datetime import UTC, datetime

import pytest

from apps.api.app.domain.market_probability import Market
from apps.api.app.domain.risk_assessment import (
    OpenPosition,
    PositionStatus,
    RiskReason,
)
from apps.api.app.domain.value_assessment import (
    VALUE_ENGINE_VERSION,
    ValueAssessment,
    ValueDecision,
)
from apps.api.app.services.risk_engine import RiskEngine, RiskEngineError

NOW = datetime(2026, 9, 17, 18, tzinfo=UTC)


def value(
    decision=ValueDecision.GO,
    market=Market.TOTAL_GOALS_OVER_2_5,
):
    return ValueAssessment(
        1532,
        NOW,
        NOW,
        market,
        VALUE_ENGINE_VERSION,
        "market-probability-v1",
        "poisson",
        "poisson-v1",
        "feature-engine-v1",
        1.8,
        "test",
        NOW,
        0.8,
        3,
        0.77,
        1 / 1.8,
        1 / 0.8,
        1 / 0.77,
        4,
        21.44,
        0.386,
        1.38,
        0.97,
        decision,
        None,
    )


def pos(
    market,
    units=1,
    status=PositionStatus.PLACED,
    pid="p1",
):
    return OpenPosition(pid, 1532, market, units, status, "w1")


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        (ValueDecision.NO_GO, 0),
        (ValueDecision.OBSERVAR, 0),
        (ValueDecision.GO_CONDICIONAL, 0.5),
        (ValueDecision.GO, 1),
        (ValueDecision.GO_FORTE, 1.5),
        (ValueDecision.GO_PROTEGIDO, 1),
    ],
)
def test_base_stakes(decision, expected):
    result = RiskEngine().calculate(
        value=value(decision),
        bankroll_amount=500,
        exposure_known=True,
    )
    assert (
        result.base_stake_units,
        result.final_stake_units,
        result.unit_value,
        result.stake_amount,
    ) == (expected, expected, 5, expected * 5)


def test_non_placed_do_not_consume():
    positions = (
        pos(
            Market.TOTAL_GOALS_OVER_1_5,
            status=PositionStatus.RECOMMENDED,
        ),
        pos(Market.BTTS_YES, status=PositionStatus.SETTLED, pid="p2"),
    )
    result = RiskEngine().calculate(
        value=value(),
        bankroll_amount=500,
        positions=positions,
        exposure_known=True,
        wallet_id="w1",
    )
    assert result.current_match_exposure_units == 0
    assert result.final_stake_units == 1


def test_unknown_exposure_warns_without_reducing():
    result = RiskEngine().calculate(value=value(), bankroll_amount=500)
    assert result.final_stake_units == 1
    assert result.exposure_warning
    assert result.reason is RiskReason.UNREGISTERED_EXPOSURE_WARNING


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (Market.TOTAL_GOALS_OVER_1_5, Market.TOTAL_GOALS_OVER_2_5),
        (Market.TOTAL_GOALS_OVER_1_5, Market.BTTS_YES),
        (Market.TOTAL_GOALS_OVER_2_5, Market.BTTS_YES),
        (Market.TOTAL_GOALS_UNDER_3_5, Market.TOTAL_GOALS_UNDER_4_5),
    ],
)
def test_correlation(first, second):
    result = RiskEngine().calculate(
        value=value(ValueDecision.GO, second),
        bankroll_amount=500,
        positions=(pos(first),),
        exposure_known=True,
        wallet_id="w1",
    )
    assert result.final_stake_units == 0.5
    assert result.reason is RiskReason.HIGH_CORRELATION_EXPOSURE


def test_go_forte_correlation():
    result = RiskEngine().calculate(
        value=value(ValueDecision.GO_FORTE),
        bankroll_amount=500,
        positions=(pos(Market.TOTAL_GOALS_OVER_1_5),),
        exposure_known=True,
        wallet_id="w1",
    )
    assert result.final_stake_units == 0.75


def test_opposing():
    result = RiskEngine().calculate(
        value=value(ValueDecision.GO, Market.BTTS_NO),
        bankroll_amount=500,
        positions=(pos(Market.BTTS_YES),),
        exposure_known=True,
        wallet_id="w1",
    )
    assert result.final_stake_units == 0.5
    assert result.reason is RiskReason.OPPOSING_MARKET_EXPOSURE


def test_limits():
    full = RiskEngine().calculate(
        value=value(),
        bankroll_amount=500,
        positions=(pos(Market.BTTS_YES, 3),),
        exposure_known=True,
        wallet_id="w1",
    )
    assert full.final_stake_units == 0
    assert full.reason is RiskReason.MATCH_EXPOSURE_LIMIT_REACHED

    insufficient = RiskEngine().calculate(
        value=value(),
        bankroll_amount=500,
        positions=(pos(Market.BTTS_YES, 2.75),),
        exposure_known=True,
        wallet_id="w1",
    )
    assert insufficient.final_stake_units == 0
    assert insufficient.reason is RiskReason.INSUFFICIENT_REMAINING_CAPACITY

    capped = RiskEngine().calculate(
        value=value(ValueDecision.GO_FORTE),
        bankroll_amount=500,
        positions=(pos(Market.BTTS_NO, 2.25),),
        exposure_known=True,
        wallet_id="w1",
    )
    assert capped.final_stake_units == 0.75
    assert capped.current_match_exposure_units + capped.final_stake_units == 3


@pytest.mark.parametrize("bankroll", [0, -1, float("nan"), float("inf")])
def test_bad_bankroll(bankroll):
    with pytest.raises(RiskEngineError) as exc:
        RiskEngine().calculate(value=value(), bankroll_amount=bankroll)
    assert exc.value.reason is RiskReason.INVALID_BANKROLL


@pytest.mark.parametrize("unit", [0, -0.01, 0.02, float("nan")])
def test_bad_unit(unit):
    with pytest.raises(RiskEngineError) as exc:
        RiskEngine().calculate(
            value=value(),
            bankroll_amount=500,
            unit_percent=unit,
        )
    assert exc.value.reason is RiskReason.INVALID_UNIT_PERCENT


def test_bad_version():
    with pytest.raises(RiskEngineError) as exc:
        RiskEngine().calculate(
            value=replace(value(), value_engine_version="future"),
            bankroll_amount=500,
        )
    assert exc.value.reason is RiskReason.INCOMPATIBLE_VALUE_ENGINE_VERSION


def test_invalid_placed_stake_fails():
    with pytest.raises(RiskEngineError) as exc:
        RiskEngine().calculate(
            value=value(),
            bankroll_amount=500,
            positions=(pos(Market.BTTS_YES, float("nan")),),
        )
    assert exc.value.reason is RiskReason.INVALID_POSITION


def test_invalid_recommended_payload_also_fails_closed():
    invalid = pos(
        Market.BTTS_YES,
        float("nan"),
        PositionStatus.RECOMMENDED,
    )
    with pytest.raises(RiskEngineError) as exc:
        RiskEngine().calculate(
            value=value(),
            bankroll_amount=500,
            positions=(invalid,),
        )
    assert exc.value.reason is RiskReason.INVALID_POSITION


def test_hash_stable_order_independent():
    first = pos(Market.BTTS_YES, 0.5, pid="p1")
    second = pos(Market.TOTAL_GOALS_OVER_1_5, 0.5, pid="p2")
    engine = RiskEngine()
    left = engine.calculate(
        value=value(),
        bankroll_amount=500,
        positions=(first, second),
        exposure_known=True,
        wallet_id="w1",
    )
    right = engine.calculate(
        value=value(),
        bankroll_amount=500,
        positions=(second, first),
        exposure_known=True,
        wallet_id="w1",
    )
    assert left.semantic_hash == right.semantic_hash
    assert left.final_stake_units == right.final_stake_units


def test_non_correlated_placed_only_consumes_capacity():
    result = RiskEngine().calculate(
        value=value(ValueDecision.GO, Market.TOTAL_GOALS_OVER_1_5),
        bankroll_amount=500,
        positions=(pos(Market.TOTAL_GOALS_UNDER_4_5, 1),),
        exposure_known=True,
        wallet_id="w1",
    )
    assert result.final_stake_units == 1
    assert result.current_match_exposure_units == 1


def test_authoritative_zero_exposure_has_no_warning():
    result = RiskEngine().calculate(
        value=value(),
        bankroll_amount=500,
        exposure_known=True,
    )
    assert result.final_stake_units == 1
    assert result.exposure_warning is None
    assert result.reason is RiskReason.NO_CONFIRMED_EXPOSURE


def test_wrong_match_placed_fails_closed():
    position = replace(pos(Market.BTTS_YES), match_id=999)
    with pytest.raises(RiskEngineError) as exc:
        RiskEngine().calculate(
            value=value(),
            bankroll_amount=500,
            positions=(position,),
        )
    assert exc.value.reason is RiskReason.INVALID_POSITION


def test_value_semantic_identity_changes_with_bets6_input():
    engine = RiskEngine()
    first = engine.calculate(
        value=value(),
        bankroll_amount=500,
        exposure_known=True,
    )
    changed_value = replace(value(), market_odd=1.9)
    second = engine.calculate(
        value=changed_value,
        bankroll_amount=500,
        exposure_known=True,
    )
    assert first.value_semantic_hash != second.value_semantic_hash
    assert first.semantic_hash != second.semantic_hash
