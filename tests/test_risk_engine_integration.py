from datetime import UTC, datetime
from apps.api.app.domain.market_probability import MARKET_ENGINE_VERSION, Market, MarketProbabilityResult
from apps.api.app.domain.value_assessment import ValueDecision
from apps.api.app.services.risk_engine import RiskEngine
from apps.api.app.services.value_engine import ValueEngine

def test_bets6_to_bets7_preserves_identity_and_versions():
 now=datetime(2026,9,17,18,tzinfo=UTC)
 probability=MarketProbabilityResult(match_id=1532,as_of=now,calculated_at=now,market=Market.TOTAL_GOALS_OVER_1_5,market_engine_version=MARKET_ENGINE_VERSION,model_name="poisson",model_version="poisson-v1",feature_engine_version="feature-engine-v1",p_model=.90,fair_odds=1/.90)
 value=ValueEngine().calculate(probability=probability,market_odd=1.20,uncertainty_margin_pp=2,odd_source="test",odd_observed_at=now)
 assert value.decision is ValueDecision.GO_PROTEGIDO
 risk=RiskEngine().calculate(value=value,bankroll_amount=500,exposure_known=True)
 assert risk.match_id==value.match_id
 assert risk.as_of==value.as_of
 assert risk.market==value.market
 assert risk.value_engine_version==value.value_engine_version
 assert risk.market_engine_version==value.market_engine_version
 assert risk.model_version==value.model_version
 assert risk.feature_engine_version==value.feature_engine_version
 assert risk.final_stake_units==1
 assert risk.stake_amount==5
