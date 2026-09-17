from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import UTC, datetime

from apps.api.app.domain.market_probability import Market
from apps.api.app.domain.risk_assessment import RISK_ENGINE_VERSION, OpenPosition, PositionStatus, RiskAssessment, RiskDecision, RiskReason
from apps.api.app.domain.value_assessment import VALUE_ENGINE_VERSION, ValueAssessment, ValueDecision

logger = logging.getLogger(__name__)
DEFAULT_UNIT_PERCENT = 0.01
MAX_MATCH_EXPOSURE_UNITS = 3.0
MIN_STAKE_UNITS = 0.5
CORRELATION_FACTOR = 0.5
BASE_STAKE = {ValueDecision.NO_GO: 0.0, ValueDecision.OBSERVAR: 0.0, ValueDecision.GO_CONDICIONAL: .5, ValueDecision.GO: 1.0, ValueDecision.GO_FORTE: 1.5, ValueDecision.GO_PROTEGIDO: 1.0}
HIGH_CORRELATION = {frozenset((Market.TOTAL_GOALS_OVER_1_5, Market.TOTAL_GOALS_OVER_2_5)), frozenset((Market.TOTAL_GOALS_OVER_1_5, Market.BTTS_YES)), frozenset((Market.TOTAL_GOALS_OVER_2_5, Market.BTTS_YES)), frozenset((Market.TOTAL_GOALS_UNDER_3_5, Market.TOTAL_GOALS_UNDER_4_5))}
OPPOSING = {frozenset((Market.BTTS_YES, Market.BTTS_NO))}
EXPOSURE_WARNING = "Stake calculada sem exposição confirmada registrada. Se já houver aposta aberta nesta partida ou mercado correlacionado fora do sistema, recalcular/reduzir a stake antes da entrada."

class RiskEngineError(ValueError):
    def __init__(self, reason: RiskReason): super().__init__(reason.value); self.reason = reason

def _finite(v): return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))
def _f(v): return format(float(v), ".17g")

class RiskEngine:
    def calculate(self, *, value: ValueAssessment, bankroll_amount: float, unit_percent: float = DEFAULT_UNIT_PERCENT, positions: tuple[OpenPosition, ...] = (), exposure_known: bool = False, wallet_id: str | None = None) -> RiskAssessment:
        try: result = self._calculate(value, bankroll_amount, unit_percent, positions, exposure_known, wallet_id)
        except RiskEngineError as exc:
            logger.warning("risk_assessment_blocked", extra={"match_id": getattr(value, "match_id", None), "market": str(getattr(value, "market", None)), "risk_engine_version": RISK_ENGINE_VERSION, "reason": exc.reason.value}); raise
        logger.info("risk_assessment_calculated", extra={"match_id": result.match_id, "market": result.market.value, "risk_engine_version": result.risk_engine_version, "value_decision": result.value_decision.value, "bankroll_amount": result.bankroll_amount, "unit_value": result.unit_value, "base_stake_units": result.base_stake_units, "current_match_exposure_units": result.current_match_exposure_units, "correlation_adjustment": result.correlation_adjustment, "final_stake_units": result.final_stake_units, "stake_amount": result.stake_amount, "exposure_known": result.exposure_known, "risk_decision": result.risk_decision.value, "reason": result.reason.value}); return result

    def _calculate(self, value, bankroll, unit_percent, positions, exposure_known, wallet_id):
        if not isinstance(value, ValueAssessment): raise RiskEngineError(RiskReason.INVALID_VALUE_ASSESSMENT)
        if value.value_engine_version != VALUE_ENGINE_VERSION: raise RiskEngineError(RiskReason.INCOMPATIBLE_VALUE_ENGINE_VERSION)
        if value.match_id <= 0 or value.as_of.tzinfo is None or value.as_of.utcoffset() is None: raise RiskEngineError(RiskReason.INVALID_VALUE_ASSESSMENT)
        if not _finite(bankroll) or float(bankroll) <= 0: raise RiskEngineError(RiskReason.INVALID_BANKROLL)
        if not _finite(unit_percent) or float(unit_percent) <= 0 or abs(float(unit_percent)-DEFAULT_UNIT_PERCENT) > 1e-12: raise RiskEngineError(RiskReason.INVALID_UNIT_PERCENT)
        bankroll, unit_percent = float(bankroll), float(unit_percent); unit_value = bankroll * unit_percent; base = BASE_STAKE[value.decision]
        placed=[]
        for p in positions:
            if not isinstance(p, OpenPosition): raise RiskEngineError(RiskReason.INVALID_POSITION)
            if p.status is not PositionStatus.PLACED: continue
            if p.match_id != value.match_id or (wallet_id is not None and p.wallet_id != wallet_id) or not p.position_id.strip() or not _finite(p.stake_units) or p.stake_units < 0: raise RiskEngineError(RiskReason.INVALID_POSITION)
            placed.append(p)
        current=math.fsum(p.stake_units for p in placed); remaining=max(0.0, MAX_MATCH_EXPOSURE_UNITS-current); warning=None if exposure_known else EXPOSURE_WARNING
        if base == 0: return self._result(value, bankroll, unit_percent, unit_value, base, current, remaining, 1, 0, exposure_known, warning, RiskDecision.NO_POSITION, RiskReason.VALUE_NOT_GO, placed)
        if remaining <= 0: return self._result(value, bankroll, unit_percent, unit_value, base, current, remaining, 1, 0, exposure_known, warning, RiskDecision.NO_POSITION, RiskReason.MATCH_EXPOSURE_LIMIT_REACHED, placed)
        correlated=any(frozenset((value.market,p.market)) in HIGH_CORRELATION for p in placed); opposing=any(frozenset((value.market,p.market)) in OPPOSING for p in placed); factor=CORRELATION_FACTOR if correlated or opposing else 1.0; final=min(base*factor, remaining)
        if final < MIN_STAKE_UNITS: return self._result(value, bankroll, unit_percent, unit_value, base, current, remaining, factor, 0, exposure_known, warning, RiskDecision.NO_POSITION, RiskReason.INSUFFICIENT_REMAINING_CAPACITY, placed)
        if final < base: decision=RiskDecision.REDUCED_STAKE; reason=RiskReason.OPPOSING_MARKET_EXPOSURE if opposing else (RiskReason.HIGH_CORRELATION_EXPOSURE if correlated else RiskReason.CONFIRMED_EXPOSURE_PRESENT)
        else: decision=RiskDecision.FULL_STAKE; reason=RiskReason.NO_CONFIRMED_EXPOSURE if not placed else RiskReason.CONFIRMED_EXPOSURE_PRESENT
        if not exposure_known and not placed and decision is RiskDecision.FULL_STAKE: reason=RiskReason.UNREGISTERED_EXPOSURE_WARNING
        return self._result(value, bankroll, unit_percent, unit_value, base, current, remaining, factor, final, exposure_known, warning, decision, reason, placed)

    @staticmethod
    def _result(v,b,up,uv,base,current,remaining,factor,final,known,warning,decision,reason,placed):
        semantic={"match_id":v.match_id,"as_of":v.as_of.astimezone(UTC).isoformat(),"market":v.market.value,"risk_engine_version":RISK_ENGINE_VERSION,"value_engine_version":v.value_engine_version,"market_engine_version":v.market_engine_version,"model_version":v.model_version,"feature_engine_version":v.feature_engine_version,"value_decision":v.decision.value,"bankroll_amount":_f(b),"unit_percent":_f(up),"exposure_known":known,"positions":[{"position_id":p.position_id,"market":p.market.value,"stake_units":_f(p.stake_units),"status":p.status.value,"wallet_id":p.wallet_id} for p in sorted(placed,key=lambda x:(x.position_id,x.market.value,_f(x.stake_units),x.wallet_id or ""))]}
        semantic_hash=hashlib.sha256(json.dumps(semantic,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
        return RiskAssessment(v.match_id,v.as_of.astimezone(UTC),datetime.now(UTC),v.market,RISK_ENGINE_VERSION,v.value_engine_version,v.market_engine_version,v.model_version,v.feature_engine_version,v.decision,b,up,uv,base,current,MAX_MATCH_EXPOSURE_UNITS,remaining,factor,final,uv*final,known,warning,decision,reason,semantic_hash)
