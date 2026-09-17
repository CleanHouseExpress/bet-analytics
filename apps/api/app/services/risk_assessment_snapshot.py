from __future__ import annotations
import json
from dataclasses import asdict
from sqlalchemy import text
from sqlalchemy.engine import Connection
from apps.api.app.domain.risk_assessment import OpenPosition,RiskAssessment
class RiskAssessmentConflictError(RuntimeError): pass
def _jsonable(v):
 if hasattr(v,"isoformat"): return v.isoformat()
 if hasattr(v,"value"): return v.value
 return v
def _canonical_positions(positions): return sorted([{"position_id":p.position_id,"match_id":p.match_id,"market":p.market.value,"stake_units":p.stake_units,"status":p.status.value,"wallet_id":p.wallet_id} for p in positions if p.status.value=="PLACED"],key=lambda x:(x["position_id"],x["market"],format(float(x["stake_units"]),".17g"),x["wallet_id"] or ""))
def persist_risk_assessment(conn:Connection,assessment:RiskAssessment,positions:tuple[OpenPosition,...])->int:
 payload={k:_jsonable(v) for k,v in asdict(assessment).items()}; pos=_canonical_positions(positions)
 existing=conn.execute(text("SELECT id, assessment_json, positions_json FROM risk_assessment_snapshots WHERE semantic_hash=:h"),{"h":assessment.semantic_hash}).mappings().first()
 if existing:
  old=existing["assessment_json"]; old_pos=existing["positions_json"]
  if isinstance(old,str): old=json.loads(old)
  if isinstance(old_pos,str): old_pos=json.loads(old_pos)
  old=dict(old); old.pop("calculated_at",None); current=dict(payload); current.pop("calculated_at",None)
  if old!=current or old_pos!=pos: raise RiskAssessmentConflictError("RISK_ASSESSMENT_SEMANTIC_CONFLICT")
  return int(existing["id"])
 sql_pg="INSERT INTO risk_assessment_snapshots (semantic_hash,match_id,as_of,market,risk_engine_version,value_engine_version,market_engine_version,model_version,feature_engine_version,bankroll_amount,unit_percent,exposure_known,positions_json,assessment_json,calculated_at) VALUES (:h,:match_id,:as_of,:market,:risk,:value,:market_engine,:model,:feature,:bankroll,:unit,:known,CAST(:positions AS JSON),CAST(:assessment AS JSON),:calculated_at) RETURNING id"
 sql_other=sql_pg.replace("CAST(:positions AS JSON)",":positions").replace("CAST(:assessment AS JSON)",":assessment")
 row=conn.execute(text(sql_pg if conn.dialect.name=="postgresql" else sql_other),{"h":assessment.semantic_hash,"match_id":assessment.match_id,"as_of":assessment.as_of,"market":assessment.market.value,"risk":assessment.risk_engine_version,"value":assessment.value_engine_version,"market_engine":assessment.market_engine_version,"model":assessment.model_version,"feature":assessment.feature_engine_version,"bankroll":assessment.bankroll_amount,"unit":assessment.unit_percent,"known":assessment.exposure_known,"positions":json.dumps(pos,sort_keys=True),"assessment":json.dumps(payload,sort_keys=True),"calculated_at":assessment.calculated_at}).scalar_one(); return int(row)
