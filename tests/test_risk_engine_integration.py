from datetime import UTC, datetime

from apps.api.app.domain.features import (
    CompetitionBaseline,
    FeatureSet,
    FormWindow,
    StrengthFeatures,
)
from apps.api.app.domain.market_probability import (
    MARKET_ENGINE_VERSION,
    Market,
    MarketProbabilityResult,
)
from apps.api.app.domain.value_assessment import ValueDecision
from apps.api.app.services.market_probability import MarketProbabilityEngine
from apps.api.app.services.poisson_model import PoissonModel
from apps.api.app.services.risk_engine import RiskEngine
from apps.api.app.services.value_engine import ValueEngine


def test_bets6_to_bets7_preserves_identity_and_versions():
    now = datetime(2026, 9, 17, 18, tzinfo=UTC)
    probability = MarketProbabilityResult(
        match_id=1532,
        as_of=now,
        calculated_at=now,
        market=Market.TOTAL_GOALS_OVER_1_5,
        market_engine_version=MARKET_ENGINE_VERSION,
        model_name="poisson",
        model_version="poisson-v1",
        feature_engine_version="feature-engine-v1",
        p_model=0.90,
        fair_odds=1 / 0.90,
    )
    value = ValueEngine().calculate(
        probability=probability,
        market_odd=1.20,
        uncertainty_margin_pp=2,
        odd_source="test",
        odd_observed_at=now,
    )
    assert value.decision is ValueDecision.GO_PROTEGIDO
    risk = RiskEngine().calculate(
        value=value,
        bankroll_amount=500,
        exposure_known=True,
    )
    assert risk.match_id == value.match_id
    assert risk.as_of == value.as_of
    assert risk.market == value.market
    assert risk.value_engine_version == value.value_engine_version
    assert risk.market_engine_version == value.market_engine_version
    assert risk.model_version == value.model_version
    assert risk.feature_engine_version == value.feature_engine_version
    assert risk.final_stake_units == 1
    assert risk.stake_amount == 5
    assert len(risk.value_semantic_hash) == 64



def test_bets3_to_bets7_full_chain():
    now = datetime(2026, 9, 17, 18, tzinfo=UTC)
    form = FormWindow(10, 1.5, 1.0, 1.8, 5, 3, 2, True)
    features = FeatureSet(
        match_id=1532,
        as_of=now,
        calculated_at=now,
        context_classifier_version="match-context-v1",
        feature_engine_version="feature-engine-v1",
        home_last5=form,
        home_last10=form,
        away_last5=form,
        away_last10=form,
        home_home5=form,
        home_home10=form,
        away_away5=form,
        away_away10=form,
        competition_baseline=CompetitionBaseline(240, 1.5, 1.0, 2.5),
        strengths=StrengthFeatures(1.2, 0.8, 0.9, 1.1),
        reasons=(),
    )

    poisson = PoissonModel().calculate(features=features)
    probability = MarketProbabilityEngine().calculate(
        poisson=poisson,
        market=Market.TOTAL_GOALS_OVER_1_5,
    )
    value = ValueEngine().calculate(
        probability=probability,
        market_odd=1.50,
        uncertainty_margin_pp=2,
        odd_source="full-chain-test",
        odd_observed_at=now,
    )
    risk = RiskEngine().calculate(
        value=value,
        bankroll_amount=500,
        exposure_known=True,
    )

    assert poisson.match_id == features.match_id
    assert probability.match_id == poisson.match_id
    assert value.match_id == probability.match_id
    assert risk.match_id == value.match_id
    assert probability.model_version == poisson.model_version
    assert value.market_engine_version == probability.market_engine_version
    assert risk.value_engine_version == value.value_engine_version
    assert risk.feature_engine_version == features.feature_engine_version
    assert len(risk.value_semantic_hash) == 64
    assert len(risk.semantic_hash) == 64
