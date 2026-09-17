"""risk assessment snapshots

Revision ID: 0012_risk_assessment_snapshots
Revises: 0011_value_assess_snapshots
"""
from alembic import op
import sqlalchemy as sa
revision="0012_risk_assessment_snapshots"
down_revision="0011_value_assess_snapshots"
branch_labels=None
depends_on=None

def upgrade():
 op.create_table("risk_assessment_snapshots",
  sa.Column("id",sa.BigInteger(),primary_key=True,autoincrement=True),
  sa.Column("semantic_hash",sa.String(64),nullable=False),
  sa.Column("match_id",sa.BigInteger(),nullable=False),sa.Column("as_of",sa.DateTime(timezone=True),nullable=False),sa.Column("market",sa.String(64),nullable=False),
  sa.Column("risk_engine_version",sa.String(64),nullable=False),sa.Column("value_engine_version",sa.String(64),nullable=False),sa.Column("market_engine_version",sa.String(64),nullable=False),sa.Column("model_version",sa.String(64),nullable=False),sa.Column("feature_engine_version",sa.String(64),nullable=False),
  sa.Column("bankroll_amount",sa.Float(),nullable=False),sa.Column("unit_percent",sa.Float(),nullable=False),sa.Column("exposure_known",sa.Boolean(),nullable=False),sa.Column("positions_json",sa.JSON(),nullable=False),sa.Column("assessment_json",sa.JSON(),nullable=False),sa.Column("calculated_at",sa.DateTime(timezone=True),nullable=False),
  sa.UniqueConstraint("semantic_hash",name="uq_risk_assessment_snapshots_semantic_hash"))
 op.create_index("ix_risk_assessment_snapshots_match_asof","risk_assessment_snapshots",["match_id","as_of"])

def downgrade():
 op.drop_index("ix_risk_assessment_snapshots_match_asof",table_name="risk_assessment_snapshots"); op.drop_table("risk_assessment_snapshots")
