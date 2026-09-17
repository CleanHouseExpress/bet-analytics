from dataclasses import replace
from datetime import UTC,datetime
import pytest
from apps.api.app.domain.market_probability import Market
from apps.api.app.domain.risk_assessment import OpenPosition,PositionStatus,RiskReason
from apps.api.app.domain.value_assessment import VALUE_ENGINE_VERSION,ValueAssessment,ValueDecision
from apps.api.app.services.risk_engine import RiskEngine,RiskEngineError
NOW=datetime(2026,9,17,18,tzinfo=UTC)
def value(decision=ValueDecision.GO,market=Market.TOTAL_GOALS_OVER_2_5): return ValueAssessment(1532,NOW,NOW,market,VALUE_ENGINE_VERSION,"market-probability-v1","poisson","poisson-v1","feature-engine-v1",1.8,"test",NOW,.8,3,.77,1/1.8,1/.8,1/.77,4,21.44,.386,1.38,.97,decision,None)
def pos(market,units=1,status=PositionStatus.PLACED,pid="p1"): return OpenPosition(pid,1532,market,units,status,"w1")
@pytest.mark.parametrize("d,e",[(ValueDecision.NO_GO,0),(ValueDecision.OBSERVAR,0),(ValueDecision.GO_CONDICIONAL,.5),(ValueDecision.GO,1),(ValueDecision.GO_FORTE,1.5),(ValueDecision.GO_PROTEGIDO,1)])
def test_base_stakes(d,e):
 r=RiskEngine().calculate(value=value(d),bankroll_amount=500,exposure_known=True); assert (r.base_stake_units,r.final_stake_units,r.unit_value,r.stake_amount)==(e,e,5,e*5)
def test_non_placed_do_not_consume():
 ps=(pos(Market.TOTAL_GOALS_OVER_1_5,status=PositionStatus.RECOMMENDED),pos(Market.BTTS_YES,status=PositionStatus.SETTLED,pid="p2")); r=RiskEngine().calculate(value=value(),bankroll_amount=500,positions=ps,exposure_known=True,wallet_id="w1"); assert r.current_match_exposure_units==0 and r.final_stake_units==1
def test_unknown_exposure_warns_without_reducing():
 r=RiskEngine().calculate(value=value(),bankroll_amount=500); assert r.final_stake_units==1 and r.exposure_warning and r.reason is RiskReason.UNREGISTERED_EXPOSURE_WARNING
@pytest.mark.parametrize("a,b",[(Market.TOTAL_GOALS_OVER_1_5,Market.TOTAL_GOALS_OVER_2_5),(Market.TOTAL_GOALS_OVER_1_5,Market.BTTS_YES),(Market.TOTAL_GOALS_OVER_2_5,Market.BTTS_YES),(Market.TOTAL_GOALS_UNDER_3_5,Market.TOTAL_GOALS_UNDER_4_5)])
def test_correlation(a,b):
 r=RiskEngine().calculate(value=value(ValueDecision.GO,b),bankroll_amount=500,positions=(pos(a),),exposure_known=True,wallet_id="w1"); assert r.final_stake_units==.5 and r.reason is RiskReason.HIGH_CORRELATION_EXPOSURE
def test_go_forte_correlation(): assert RiskEngine().calculate(value=value(ValueDecision.GO_FORTE),bankroll_amount=500,positions=(pos(Market.TOTAL_GOALS_OVER_1_5),),exposure_known=True,wallet_id="w1").final_stake_units==.75
def test_opposing():
 r=RiskEngine().calculate(value=value(ValueDecision.GO,Market.BTTS_NO),bankroll_amount=500,positions=(pos(Market.BTTS_YES),),exposure_known=True,wallet_id="w1"); assert r.final_stake_units==.5 and r.reason is RiskReason.OPPOSING_MARKET_EXPOSURE
def test_limits():
 r=RiskEngine().calculate(value=value(),bankroll_amount=500,positions=(pos(Market.BTTS_YES,3),),exposure_known=True,wallet_id="w1"); assert r.final_stake_units==0 and r.reason is RiskReason.MATCH_EXPOSURE_LIMIT_REACHED
 r=RiskEngine().calculate(value=value(),bankroll_amount=500,positions=(pos(Market.BTTS_YES,2.75),),exposure_known=True,wallet_id="w1"); assert r.final_stake_units==0 and r.reason is RiskReason.INSUFFICIENT_REMAINING_CAPACITY
 r=RiskEngine().calculate(value=value(ValueDecision.GO_FORTE),bankroll_amount=500,positions=(pos(Market.BTTS_NO,2.25),),exposure_known=True,wallet_id="w1"); assert r.final_stake_units==.75 and r.current_match_exposure_units+r.final_stake_units==3
@pytest.mark.parametrize("b",[0,-1,float("nan"),float("inf")])
def test_bad_bankroll(b):
 with pytest.raises(RiskEngineError) as e: RiskEngine().calculate(value=value(),bankroll_amount=b)
 assert e.value.reason is RiskReason.INVALID_BANKROLL
@pytest.mark.parametrize("u",[0,-.01,.02,float("nan")])
def test_bad_unit(u):
 with pytest.raises(RiskEngineError) as e: RiskEngine().calculate(value=value(),bankroll_amount=500,unit_percent=u)
 assert e.value.reason is RiskReason.INVALID_UNIT_PERCENT
def test_bad_version():
 with pytest.raises(RiskEngineError) as e: RiskEngine().calculate(value=replace(value(),value_engine_version="future"),bankroll_amount=500)
 assert e.value.reason is RiskReason.INCOMPATIBLE_VALUE_ENGINE_VERSION
def test_invalid_placed_stake_fails():
 with pytest.raises(RiskEngineError) as e: RiskEngine().calculate(value=value(),bankroll_amount=500,positions=(pos(Market.BTTS_YES,float("nan")),))
 assert e.value.reason is RiskReason.INVALID_POSITION
def test_invalid_recommended_payload_also_fails_closed():
 with pytest.raises(RiskEngineError) as e: RiskEngine().calculate(value=value(),bankroll_amount=500,positions=(pos(Market.BTTS_YES,float("nan"),PositionStatus.RECOMMENDED),))
 assert e.value.reason is RiskReason.INVALID_POSITION
def test_hash_stable_order_independent():
 a=pos(Market.BTTS_YES,.5,pid="p1"); b=pos(Market.TOTAL_GOALS_OVER_1_5,.5,pid="p2"); eng=RiskEngine(); x=eng.calculate(value=value(),bankroll_amount=500,positions=(a,b),exposure_known=True,wallet_id="w1"); y=eng.calculate(value=value(),bankroll_amount=500,positions=(b,a),exposure_known=True,wallet_id="w1"); assert x.semantic_hash==y.semantic_hash and x.final_stake_units==y.final_stake_units
def test_non_correlated_placed_only_consumes_capacity():
 r=RiskEngine().calculate(value=value(ValueDecision.GO,Market.TOTAL_GOALS_OVER_1_5),bankroll_amount=500,positions=(pos(Market.TOTAL_GOALS_UNDER_4_5,1),),exposure_known=True,wallet_id="w1"); assert r.final_stake_units==1 and r.current_match_exposure_units==1
def test_authoritative_zero_exposure_has_no_warning():
 r=RiskEngine().calculate(value=value(),bankroll_amount=500,exposure_known=True); assert r.final_stake_units==1 and r.exposure_warning is None and r.reason is RiskReason.NO_CONFIRMED_EXPOSURE
def test_wrong_match_placed_fails_closed():
 p=replace(pos(Market.BTTS_YES),match_id=999)
 with pytest.raises(RiskEngineError) as e: RiskEngine().calculate(value=value(),bankroll_amount=500,positions=(p,))
 assert e.value.reason is RiskReason.INVALID_POSITION
