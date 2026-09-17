from __future__ import annotations
import hashlib,json,logging,math
from datetime import UTC,datetime
from apps.api.app.domain.market_probability import Market
from apps.api.app.domain.risk_assessment import RISK_ENGINE_VERSION,OpenPosition,PositionStatus,RiskAssessment,RiskDecision,RiskReason
from apps.api.app.domain.value_assessment import VALUE_ENGINE_VERSION,ValueAssessment,ValueDecision
logger=logging.getLogger(__name__)
DEFAULT_UNIT_PERCENT=.01; MAX_MATCH_EXPOSURE_UNITS=3.; MIN_STAKE_UNITS=.5; CORRELATION_FACTOR=.5
BASE_STAKE={ValueDecision.NO_GO:0.,ValueDecision.OBSERVAR:0.,ValueDecision.GO_CONDICIONAL:.5,ValueDecision.GO:1.,ValueDecision.GO_FORTE:1.5,ValueDecision.GO_PROTEGIDO:1.}
HIGH_CORRELATION={frozenset((Market.TOTAL_GOALS_OVER_1_5,Market.TOTAL_GOALS_OVER_2_5)),frozenset((Market.TOTAL_GOALS_OVER_1_5,Market.BTTS_YES)),frozenset((Market.TOTAL_GOALS_OVER_2_5,Market.BTTS_YES)),frozenset((Market.TOTAL_GOALS_UNDER_3_5,Market.TOTAL_GOALS_UNDER_4_5))}; OPPOSING={frozenset((Market.BTTS_YES,Market.BTTS_NO))}
EXPOSURE_WARNING="Stake calculada sem exposição confirmada registrada. Se já houver aposta aberta nesta partida ou mercado correlacionado fora do sistema, recalcular/reduzir a stake antes da entrada."
class RiskEngineError(ValueError):
 def __init__(self,reason): super().__init__(reason.value); self.reason=reason
def _finite(v): return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(float(v))
def _f(v): return format(float(v),".17g")
class RiskEngine:
 def calculate(self,*,value,bankroll_amount,unit_percent=DEFAULT_UNIT_PERCENT,positions=(),exposure_known=False,wallet_id=None):
  try:r=self._calculate(value,bankroll_amount,unit_percent,positions,exposure_known,wallet_id)
  except RiskEngineError as e: logger.warning("risk_assessment_blocked",extra={"match_id":getattr(value,"match_id",None),"market":str(getattr(value,"market",None)),"risk_engine_version":RISK_ENGINE_VERSION,"reason":e.reason.value}); raise
  logger.info("risk_assessment_calculated",extra={"match_id":r.match_id,"market":r.market.value,"risk_engine_version":r.risk_engine_version,"value_decision":r.value_decision.value,"bankroll_amount":r.bankroll_amount,"unit_value":r.unit_value,"base_stake_units":r.base_stake_units,"current_match_exposure_units":r.current_match_exposure_units,"correlation_adjustment":r.correlation_adjustment,"final_stake_units":r.final_stake_units,"stake_amount":r.stake_amount,"exposure_known":r.exposure_known,"risk_decision":r.risk_decision.value,"reason":r.reason.value}); return r
 def _calculate(self,v,b,up,positions,known,wallet):
  if not isinstance(v,ValueAssessment): raise RiskEngineError(RiskReason.INVALID_VALUE_ASSESSMENT)
  if v.value_engine_version!=VALUE_ENGINE_VERSION: raise RiskEngineError(RiskReason.INCOMPATIBLE_VALUE_ENGINE_VERSION)
  if v.match_id<=0 or v.as_of.tzinfo is None or v.as_of.utcoffset() is None or v.decision not in BASE_STAKE: raise RiskEngineError(RiskReason.INVALID_VALUE_ASSESSMENT)
  if not _finite(b) or float(b)<=0: raise RiskEngineError(RiskReason.INVALID_BANKROLL)
  if not _finite(up) or float(up)<=0 or abs(float(up)-DEFAULT_UNIT_PERCENT)>1e-12: raise RiskEngineError(RiskReason.INVALID_UNIT_PERCENT)
  b=float(b); up=float(up); uv=b*up; base=BASE_STAKE[v.decision]; placed=[]
  for p in positions:
   if not isinstance(p,OpenPosition): raise RiskEngineError(RiskReason.INVALID_POSITION)
   if p.status is not PositionStatus.PLACED: continue
   if p.match_id!=v.match_id or (wallet is not None and p.wallet_id!=wallet) or not p.position_id.strip() or not _finite(p.stake_units) or p.stake_units<0: raise RiskEngineError(RiskReason.INVALID_POSITION)
   placed.append(p)
  current=math.fsum(p.stake_units for p in placed); remaining=max(0.,MAX_MATCH_EXPOSURE_UNITS-current); warning=None if known else EXPOSURE_WARNING
  if base==0:return self._result(v,b,up,uv,base,current,remaining,1.,0.,known,warning,RiskDecision.NO_POSITION,RiskReason.VALUE_NOT_GO,placed)
  if remaining<=0:return self._result(v,b,up,uv,base,current,remaining,1.,0.,known,warning,RiskDecision.NO_POSITION,RiskReason.MATCH_EXPOSURE_LIMIT_REACHED,placed)
  corr=any(frozenset((v.market,p.market)) in HIGH_CORRELATION for p in placed); opp=any(frozenset((v.market,p.market)) in OPPOSING for p in placed); factor=CORRELATION_FACTOR if corr or opp else 1.; final=min(base*factor,remaining)
  if final<MIN_STAKE_UNITS:return self._result(v,b,up,uv,base,current,remaining,factor,0.,known,warning,RiskDecision.NO_POSITION,RiskReason.INSUFFICIENT_REMAINING_CAPACITY,placed)
  if final<base: decision=RiskDecision.REDUCED_STAKE; reason=RiskReason.OPPOSING_MARKET_EXPOSURE if opp else (RiskReason.HIGH_CORRELATION_EXPOSURE if corr else RiskReason.CONFIRMED_EXPOSURE_PRESENT)
  else: decision=RiskDecision.FULL_STAKE; reason=RiskReason.NO_CONFIRMED_EXPOSURE if not placed else RiskReason.CONFIRMED_EXPOSURE_PRESENT
  if not known and not placed and decision is RiskDecision.FULL_STAKE: reason=RiskReason.UNREGISTERED_EXPOSURE_WARNING
  return self._result(v,b,up,uv,base,current,remaining,factor,final,known,warning,decision,reason,placed)
 @staticmethod
 def _result(v,b,up,uv,base,current,remaining,factor,final,known,warning,decision,reason,placed):
  semantic={"match_id":v.match_id,"as_of":v.as_of.astimezone(UTC).isoformat(),"market":v.market.value,"risk_engine_version":RISK_ENGINE_VERSION,"value_engine_version":v.value_engine_version,"market_engine_version":v.market_engine_version,"model_version":v.model_version,"feature_engine_version":v.feature_engine_version,"value_decision":v.decision.value,"bankroll_amount":_f(b),"unit_percent":_f(up),"exposure_known":known,"positions":[{"position_id":p.position_id,"market":p.market.value,"stake_units":_f(p.stake_units),"status":p.status.value,"wallet_id":p.wallet_id} for p in sorted(placed,key=lambda x:(x.position_id,x.market.value,_f(x.stake_units),x.wallet_id or ""))]}; h=hashlib.sha256(json.dumps(semantic,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
  return RiskAssessment(v.match_id,v.as_of.astimezone(UTC),datetime.now(UTC),v.market,RISK_ENGINE_VERSION,v.value_engine_version,v.market_engine_version,v.model_version,v.feature_engine_version,v.decision,b,up,uv,base,current,MAX_MATCH_EXPOSURE_UNITS,remaining,factor,final,uv*final,known,warning,decision,reason,h)
