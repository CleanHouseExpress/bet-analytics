from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from apps.api.app.domain.backtest import (
    BacktestEvaluation,
    BacktestEvaluationStatus,
    BacktestPromotionStatus,
)
from apps.api.app.domain.decision_journal import SettlementResult
from apps.api.app.domain.market_probability import Market
from apps.api.app.domain.risk_assessment import RiskDecision
from apps.api.app.domain.value_assessment import ValueDecision
from apps.api.app.services.walk_forward_backtest import (
    BacktestDataError,
    WalkForwardBacktest,
    _market_outcome,
    _odds_row_matches_market,
)


def _evaluation(
    *,
    p_model=0.8,
    baseline_probability=0.6,
    actual=True,
    stake_value=5.0,
    profit_loss=2.5,
):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return BacktestEvaluation(
        match_id=1,
        season="2026",
        round_number=1,
        kickoff_at=now,
        as_of=now,
        market=Market.TOTAL_GOALS_OVER_1_5,
        status=BacktestEvaluationStatus.EVALUATED,
        p_model=p_model,
        baseline_probability=baseline_probability,
        actual_outcome=actual,
        model_side="HOME",
        market_odd=1.5,
        odd_source="test",
        odd_observed_at=now,
        value_decision=ValueDecision.GO,
        risk_decision=RiskDecision.FULL_STAKE,
        stake_units=1.0 if stake_value else 0.0,
        stake_value=stake_value,
        settlement_result=(
            SettlementResult.GREEN if actual and stake_value else
            SettlementResult.RED if stake_value else None
        ),
        settled_at=now,
        profit_loss=profit_loss if stake_value else None,
        journal_entry=None,
        feature_semantic_hash=None,
        poisson_semantic_hash=None,
        market_probability_semantic_hash=None,
        value_semantic_hash=None,
        risk_semantic_hash=None,
        feature_payload=None,
        block_reason=None,
    )


@pytest.mark.parametrize(
    ("market", "score", "expected"),
    [
        (Market.TOTAL_GOALS_OVER_1_5, (1, 1), True),
        (Market.TOTAL_GOALS_OVER_2_5, (1, 1), False),
        (Market.TOTAL_GOALS_UNDER_3_5, (2, 1), True),
        (Market.TOTAL_GOALS_UNDER_4_5, (4, 1), False),
        (Market.BTTS_YES, (1, 1), True),
        (Market.BTTS_NO, (1, 0), True),
    ],
)
def test_market_outcome(market, score, expected):
    assert _market_outcome(market, *score) is expected


def test_generic_total_and_btts_odds_mapping():
    total = {
        "market_code": "TOTAL_GOALS",
        "market_name": "Total Goals",
        "selection": "Over",
        "line": 1.5,
    }
    btts = {
        "market_code": "BTTS",
        "market_name": "Both Teams To Score",
        "selection": "Yes",
        "line": None,
    }
    assert _odds_row_matches_market(
        total,
        Market.TOTAL_GOALS_OVER_1_5,
    )
    assert not _odds_row_matches_market(
        total,
        Market.TOTAL_GOALS_OVER_2_5,
    )
    assert _odds_row_matches_market(btts, Market.BTTS_YES)
    assert not _odds_row_matches_market(btts, Market.BTTS_NO)


def test_price_selection_uses_latest_per_bookmaker_before_round_cutoff():
    cutoff = datetime(2026, 1, 1, 20, tzinfo=UTC)
    rows = (
        {
            "market_code": "TOTAL_GOALS_OVER_1_5",
            "market_name": "Total Goals Over 1.5",
            "selection": "over",
            "line": 1.5,
            "odd": 1.70,
            "bookmaker_name": "Book A",
            "captured_at": cutoff - timedelta(minutes=10),
        },
        {
            "market_code": "TOTAL_GOALS_OVER_1_5",
            "market_name": "Total Goals Over 1.5",
            "selection": "over",
            "line": 1.5,
            "odd": 1.60,
            "bookmaker_name": "Book B",
            "captured_at": cutoff - timedelta(minutes=1),
        },
        {
            "market_code": "TOTAL_GOALS_OVER_1_5",
            "market_name": "Total Goals Over 1.5",
            "selection": "over",
            "line": 1.5,
            "odd": 2.50,
            "bookmaker_name": "Book C",
            "captured_at": cutoff + timedelta(seconds=1),
        },
    )
    price = WalkForwardBacktest._market_price(
        rows,
        market=Market.TOTAL_GOALS_OVER_1_5,
        cutoff=cutoff,
    )
    assert price == (1.70, "Book A", cutoff - timedelta(minutes=10))


def test_duplicate_fixture_gate_fails_closed():
    kickoff = datetime(2026, 1, 1, 20, tzinfo=UTC)
    base = {
        "match_id": 1,
        "season": "2026",
        "round_number": 1,
        "home_team_id": 10,
        "away_team_id": 20,
        "kickoff_at": kickoff,
    }
    duplicate = {**base, "match_id": 2}
    with pytest.raises(BacktestDataError, match="DUPLICATE_FIXTURE"):
        WalkForwardBacktest._validate_match_set([base, duplicate])


def test_minimum_sample_gate_prevents_small_sample_promotion():
    evaluations = [
        _evaluation(p_model=0.8, baseline_probability=0.5, actual=True)
        for _ in range(10)
    ]
    metrics = WalkForwardBacktest._metrics(
        evaluations,
        initial_bankroll=500,
        ending_bankroll=525,
        equity_curve=[500, 525],
        min_sample_for_review=100,
    )
    assert metrics.brier_score < metrics.baseline_brier_score
    assert metrics.profit_loss > 0
    assert metrics.promotion_status is BacktestPromotionStatus.INSUFFICIENT_SAMPLE


def test_predictive_metrics_include_missing_odd_but_financial_metrics_do_not():
    item = _evaluation(stake_value=0.0, profit_loss=0.0)
    missing = replace(
        item,
        status=BacktestEvaluationStatus.MISSING_ODD,
        market_odd=None,
        odd_source=None,
        odd_observed_at=None,
    )
    metrics = WalkForwardBacktest._metrics(
        [missing],
        initial_bankroll=500,
        ending_bankroll=500,
        equity_curve=[500],
        min_sample_for_review=100,
    )
    assert metrics.probability_evaluations == 1
    assert metrics.missing_odd_evaluations == 1
    assert metrics.bets == 0
    assert metrics.stake_total == 0
