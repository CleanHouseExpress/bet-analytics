"""persist immutable value assessment snapshots

Revision ID: 0011_value_assess_snapshots
Revises: 0010_market_prob_snapshots
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011_value_assess_snapshots"
down_revision = "0010_market_prob_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "value_assessment_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("evaluation_key", sa.String(length=400), nullable=False),
        sa.Column("match_id", sa.BigInteger(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("market", sa.String(length=64), nullable=False),
        sa.Column("value_engine_version", sa.String(length=64), nullable=False),
        sa.Column("market_engine_version", sa.String(length=64), nullable=False),
        sa.Column("model_name", sa.String(length=64), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column("feature_engine_version", sa.String(length=64), nullable=False),
        sa.Column("market_odd", sa.Float(), nullable=False),
        sa.Column("uncertainty_margin_pp", sa.Float(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("semantic_hash", sa.String(length=64), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "evaluation_key", name="uq_value_assessment_snapshots_evaluation_key"
        ),
    )
    op.create_index(
        "ix_value_assessment_snapshots_match_as_of",
        "value_assessment_snapshots",
        ["match_id", "as_of"],
        unique=False,
    )
    op.create_index(
        "ix_value_assessment_snapshots_market_version",
        "value_assessment_snapshots",
        ["market", "value_engine_version", "market_engine_version"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_value_assessment_snapshots_market_version",
        table_name="value_assessment_snapshots",
    )
    op.drop_index(
        "ix_value_assessment_snapshots_match_as_of",
        table_name="value_assessment_snapshots",
    )
    op.drop_table("value_assessment_snapshots")
