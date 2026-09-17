from datetime import UTC,datetime
from dataclasses import replace
import json,pytest
from sqlalchemy import create_engine,text
from apps.api.app.domain.market_probability import Market
from apps.api.app.domain.risk_assessment import OpenPosition,PositionStatus
from apps.api.app.domain.value_assessment import VALUE_ENGINE_VERSION,ValueAssessment,ValueDecision
from apps.api.app.services.risk_engine import RiskEngine
from apps.api.app.services.risk_assessment_snapshot import RiskAssessmentConflictError,persist_risk_assessment
NOW=datetime(2026,9,17,18,tzinfo=UTC)
def _value(): return ValueAssessment(1532,NOW,NOW,Market.TOTAL_GOALS_OVER_2_5,VALUE_ENGINE_VERSION,"market-probability-v1","poisson","poisson-v1","feature-engine-v1",1.8,"test",NOW,.8,3,.77,1/1.8,1/.8,1/.77,4,21.44,.386,1.38,.97,ValueDecision.GO,None)
def _setup():
 e=create_engine("sqlite://"); c=e.connect(); c.execute(text("CREATE TABLE risk_assessment_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, semantic_hash TEXT UNIQUE NOT NULL, match_id INTEGER NOT NULL, as_of DATETIME NOT NULL, market TEXT NOT NULL, risk_engine_version TEXT NOT NULL, value_engine_version TEXT NOT NULL, market_engine_version TEXT NOT NULL, model_version TEXT NOT NULL, feature_engine_version TEXT NOT NULL, bankroll_amount FLOAT NOT NULL, unit_percent FLOAT NOT NULL, exposure_known BOOLEAN NOT NULL, positions_json JSON NOT NULL, assessment_json JSON NOT NULL, calculated_at DATETIME NOT NULL)")); return e,c
def test_snapshot_idempotent():
 e,c=_setup(); p=(OpenPosition("p1",1532,Market.BTTS_YES,.5,PositionStatus.PLACED,"w1"),); r=RiskEngine().calculate(value=_value(),bankroll_amount=500,positions=p,exposure_known=True,wallet_id="w1"); a=persist_risk_assessment(c,r,p); b=persist_risk_assessment(c,r,p); assert a==b and c.execute(text("SELECT count(*) FROM risk_assessment_snapshots")).scalar_one()==1; c.close(); e.dispose()
def test_same_hash_divergent_payload_conflicts():
 e,c=_setup(); r=RiskEngine().calculate(value=_value(),bankroll_amount=500,exposure_known=True); persist_risk_assessment(c,r,()); row=c.execute(text("SELECT assessment_json FROM risk_assessment_snapshots WHERE semantic_hash=:h"),{"h":r.semantic_hash}).scalar_one(); payload=json.loads(row) if isinstance(row,str) else dict(row); payload["stake_amount"]=999; c.execute(text("UPDATE risk_assessment_snapshots SET assessment_json=:p WHERE semantic_hash=:h"),{"p":json.dumps(payload),"h":r.semantic_hash});
 with pytest.raises(RiskAssessmentConflictError): persist_risk_assessment(c,r,())
 c.close(); e.dispose()
