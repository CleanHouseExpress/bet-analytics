from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from apps.api.app.domain.market_probability import Market, MarketProbabilityReason
from apps.api.app.domain.poisson import POISSON_MODEL_NAME, POISSON_MODEL_VERSION, PoissonResult
from apps.api.app.services.market_probability import MarketProbabilityEngine, MarketProbabilityError
from apps.api.app.services.market_probability_snapshot import semantic_hash


def poisson_result(*, max_goals: int = 10) -> PoissonResult:
    lh = 1.3658536585365852
    la = 0.6593406593406593
    hp = tuple(math.exp(-lh) * lh**k / math.factorial(k) for k in range(max_goals + 1))
    ap = tuple(math.exp(-la) * la**k / math.factorial(k) for k in range(max_goals + 1))
    matrix = tuple(tuple(h * a for a in ap) for h in hp)
    mass = math.fsum(math.fsum(row) for row in matrix)
    return PoissonResult(
        match_id=1532,
        as_of=datetime(2026, 9, 15, 16, tzinfo=UTC),
        calculated_at=datetime(2026, 9, 15, 16, 1, tzinfo=UTC),
        model_name=POISSON_MODEL_NAME,
        model_version=POISSON_MODEL_VERSION,
        feature_engine_version="feature-engine-v1",
        lambda_home=lh,
        lambda_away=la,
        max_goals=max_goals,
        home_goal_probabilities=hp,
        away_goal_probabilities=ap,
        score_matrix=matrix,
        matrix_probability_mass=mass,
        tail_probability=1.0 - mass,
    )


def test_generates_all_six_markets():
    results = MarketProbabilityEngine().calculate_all(poisson=poisson_result())
    assert {r.market for r in results} == set(Market)
    assert len(results) == 6


@pytest.mark.parametrize(
    ("market", "maximum", "over"),
    [
        (Market.TOTAL_GOALS_OVER_1_5, 1, True),
        (Market.TOTAL_GOALS_OVER_2_5, 2, True),
        (Market.TOTAL_GOALS_UNDER_3_5, 3, False),
        (Market.TOTAL_GOALS_UNDER_4_5, 4, False),
    ],
)
def test_total_goals_markets_match_full_poisson(market, maximum, over):
    source = poisson_result()
    lam = source.lambda_home + source.lambda_away
    cdf = sum(
        math.exp(-lam) * lam**k / math.factorial(k) for k in range(maximum + 1)
    )
    expected = 1.0 - cdf if over else cdf
    result = MarketProbabilityEngine().calculate(poisson=source, market=market)
    assert result.p_model == pytest.approx(expected, abs=1e-12)
    assert result.fair_odds == pytest.approx(1.0 / expected, abs=1e-12)


def test_btts_is_analytic_and_complementary():
    source = poisson_result()
    engine = MarketProbabilityEngine()
    yes = engine.calculate(poisson=source, market=Market.BTTS_YES)
    no = engine.calculate(poisson=source, market=Market.BTTS_NO)
    expected = (
        1
        - math.exp(-source.lambda_home)
        - math.exp(-source.lambda_away)
        + math.exp(-(source.lambda_home + source.lambda_away))
    )
    assert yes.p_model == pytest.approx(expected, abs=1e-12)
    assert no.p_model == pytest.approx(1.0 - expected, abs=1e-12)
    assert yes.p_model + no.p_model == pytest.approx(1.0, abs=1e-12)


def test_max_goals_does_not_change_published_markets():
    engine = MarketProbabilityEngine()
    small = {
        r.market: r.p_model
        for r in engine.calculate_all(poisson=poisson_result(max_goals=3))
    }
    large = {
        r.market: r.p_model
        for r in engine.calculate_all(poisson=poisson_result(max_goals=15))
    }
    assert small == pytest.approx(large, abs=1e-12)


def test_semantic_hash_ignores_calculated_at():
    result = MarketProbabilityEngine().calculate(
        poisson=poisson_result(), market=Market.TOTAL_GOALS_OVER_2_5
    )
    changed = replace(result, calculated_at=datetime(2030, 1, 1, tzinfo=UTC))
    assert semantic_hash(result) == semantic_hash(changed)


def test_invalid_mass_is_blocked():
    source = replace(poisson_result(), tail_probability=0.5)
    with pytest.raises(MarketProbabilityError) as exc:
        MarketProbabilityEngine().calculate(poisson=source, market=Market.BTTS_YES)
    assert exc.value.reason == MarketProbabilityReason.INVALID_PROBABILITY_MASS


@pytest.mark.parametrize("value", [0.0, -1.0, math.inf, -math.inf, math.nan])
def test_invalid_lambda_is_blocked(value):
    source = replace(poisson_result(), lambda_home=value)
    with pytest.raises(MarketProbabilityError) as exc:
        MarketProbabilityEngine().calculate(poisson=source, market=Market.BTTS_YES)
    assert exc.value.reason == MarketProbabilityReason.INVALID_LAMBDA


def test_incompatible_model_version_is_blocked():
    source = replace(poisson_result(), model_version="poisson-v2")
    with pytest.raises(MarketProbabilityError) as exc:
        MarketProbabilityEngine().calculate(poisson=source, market=Market.BTTS_YES)
    assert exc.value.reason == MarketProbabilityReason.INCOMPATIBLE_MODEL_VERSION


def test_unsupported_market_is_blocked():
    with pytest.raises(MarketProbabilityError) as exc:
        MarketProbabilityEngine().calculate(
            poisson=poisson_result(), market="CORNERS_OVER_9_5"
        )
    assert exc.value.reason == MarketProbabilityReason.UNSUPPORTED_MARKET


def test_success_and_blocked_logs(caplog):
    engine = MarketProbabilityEngine()
    with caplog.at_level("INFO"):
        engine.calculate(poisson=poisson_result(), market=Market.BTTS_YES)
    success = next(
        record
        for record in caplog.records
        if record.message == "market_probability_calculated"
    )
    assert success.match_id == 1532
    assert success.market == Market.BTTS_YES.value
    assert 0.0 <= success.p_model <= 1.0

    caplog.clear()
    with caplog.at_level("WARNING"), pytest.raises(MarketProbabilityError):
        engine.calculate(
            poisson=replace(poisson_result(), lambda_home=0.0),
            market=Market.BTTS_YES,
        )
    blocked = next(
        record
        for record in caplog.records
        if record.message == "market_probability_blocked"
    )
    assert blocked.reason == MarketProbabilityReason.INVALID_LAMBDA.value
