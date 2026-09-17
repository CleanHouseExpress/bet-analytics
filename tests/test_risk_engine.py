from dataclasses import replace
from datetime import UTC, datetime
import pytest
from apps.api.app.domain.market_probability import Market
from apps.api.app.domain.risk_assessment import OpenPosition, PositionStatus, RiskDecision, RiskReason
from apps.api.app.domain.value_assessment import VALUE_ENGINE_VERSION, ValueAssessment, ValueDecision
from apps.api.app.services.risk_engine import RiskEngine, RiskEngineError
NOW=datetime(2026,9,17,18,tzinfo=UTC)
def value(decision=ValueDecision.GO,market=Market.TOTAL_GOALS_OVER_2_5):
 return ValueAssessment(1532,NOW,NOW,market,VALUE_ENGINE_VERSION,"market-probability-v1","poisson","poisson-v1","feature-engine-v1",1.8,"test",NOW,.8,3,.77,1/1.8,1/.8,1/.77,4,21.44,.386,1.38,.97,decision,None)
def pos(market,units=1,status=PositionStatus.PLACED): return OpenPosition("p1",1532,market,units,status,"w1")
@pytest.mark.parametrize("decision,expected",[(ValueDecision.NO_GO,0),(ValueDecision.OBSERVAR,0),(ValueDecision.GO_CONDICIONAL,.5),(ValueDecision.GO,1),(ValueDecision.GO_FORTE,1.5),(ValueDecision.GO_PROTEGIDO,1)])
def test_base_stakes(decision,expected):
 r=RiskEngine().calculate(value=value(decision),bankroll_amount=500,exposure_known=True); assert r.base_stake_units==expected; assert r.final_stake_units==expected; assert r.unit_value==5; assert r.stake_amount==expected*5

def test_recommendations_and_settled_do_not_consume():
 ps=(pos(Market.TOTAL_GOALS_OVER_1_5,status=PositionStatus.RECOMMENDED),pos(Market.BTTS_YES,status=PositionStatus.SETTLED)); r=RiskEngine().calculate(value=value(),bankroll_amount=500,positions=ps,exposure_known=True,wallet_id="w1"); assert r.current_match_exposure_units==0; assert r.final_stake_units==1

def test_unknown_exposure_preserves_stake_and_warns():
 r=RiskEngine().calculate(value=value(),bankroll_amount=500); assert r.final_stake_units==1; assert r.exposure_warning; assert r.reason is RiskReason.UNREGISTERED_EXPOSURE_WARNING
@pytest.mark.parametrize("existing,new",[(Market.TOTAL_GOALS_OVER_1_5,Market.TOTAL_GOALS_OVER_2_5),(Market.TOTAL_GOALS_OVER_1_5,Market.BTTS_YES),(Market.TOTAL_GOALS_OVER_2_5,Market.BTTS_YES),(Market.TOTAL_GOALS_UNDER_3_5,Market.TOTAL_GOALS_UNDER_4_5)])
def test_correlation(existing,new):
 r=RiskEngine().calculate(value=value(ValueDecision.GO,new),bankroll_amount=500,positions=(pos(existing),),exposure_known=True,wallet_id="w1"); assert r.final_stake_units==.5; assert r.reason is RiskReason.HIGH_CORRELATION_EXPOSURE

def test_go_forte_correlation():
 r=RiskEngine().calculate(value=value(ValueDecision.GO_FORTE),bankroll_amount=500,positions=(pos(Market.TOTAL_GOALS_OVER_1_5),),exposure_known=True,wallet_id="w1"); assert r.final_stake_units==.75

def test_opposing_btts():
 r=RiskEngine().calculate(value=value(ValueDecision.GO,Market.BTTS_NO),bankroll_amount=500,positions=(pos(Market.BTTS_YES),),exposure_known=True,wallet_id="w1"); assert r.final_stake_units==.5; assert r.reason is RiskReason.OPPOSING_MARKET_EXPOSURE

def test_limit_and_capacity():
 r=RiskEngine().calculate(value=value(),bankroll_amount=500,positions=(pos(Market.BTTS_YES,3),),exposure_known=True,wallet_id="w1"); assert r.final_stake_units==0; assert r.reason is RiskReason.MATCH_EXPOSURE_LIMIT_REACHED
 r=RiskEngine().calculate(value=value(),bankroll_amount=500,positions=(pos(Market.BTTS_YES,2.75),),exposure_known=True,wallet_id="w1"); assert r.final_stake_units==0; assert r.reason is RiskReason.INSUFFICIENT_REMAINING_CAPACITY
 r=RiskEngine().calculate(value=value(ValueDecision.GO_FORTE),bankroll_amount=500,positions=(pos(Market.BTTS_NO,2.25),),exposure_known=True,wallet_id="w1"); assert r.final_stake_units==.75; assert r.current_match_exposure_units+r.final_stake_units==3
@pytest.mark.parametrize("bankroll",[0,-1,float("nan"),float("inf")])
def test_invalid_bankroll(bankroll):
 with pytest.raises(RiskEngineError) as e: RiskEngine().calculate(value=value(),bankroll_amount=bankroll)
 assert e.value.reason is RiskReason.INVALID_BANKROLL
@pytest.mark.parametrize("unit",[0,-.01,.02,float("nan")])
def test_invalid_unit_policy(unit):
 with pytest.raises(RiskEngineError) as e: RiskEngine().calculate(value=value(),bankroll_amount=500,unit_percent=unit)
 assert e.value.reason is RiskReason.INVALID_UNIT_PERCENT

def test_incompatible_version():
 with pytest.raises(RiskEngineError) as e: RiskEngine().calculate(value=replace(value(),value_engine_version="future"),bankroll_amount=500)
 assert e.value.reason is RiskReason.INCOMPATIBLE_VALUE_ENGINE_VERSION

def test_semantic_hash_is_stable_and_order_independent():
 a=pos(Market.BTTS_YES,.5); b=OpenPosition("p2",1532,Market.TOTAL_GOALS_OVER_1_5,.5,PositionStatus.PLACED,"w1"); engine=RiskEngine(); r1=engine.calculate(value=value(),bankroll_amount=500,positions=(a,b),exposure_known=True,wallet_id="w1"); r2=engine.calculate(value=value(),bankroll_amount=500,positions=(b,a),exposure_known=True,wallet_id="w1"); assert r1.semantic_hash==r2.semantic_hash; assert r1.final_stake_units==r2.final_stake_units
