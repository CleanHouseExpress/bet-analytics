"""persist immutable feature snapshots

Revision ID: 0007_feature_snapshots
Revises: 0006_match_context_snapshots
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007_feature_snapshots"
down_revision = "0006_match_context_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feature_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("evaluation_key", sa.String(length=160), nullable=False),
        sa.Column("match_id", sa.BigInteger(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("feature_engine_version", sa.String(length=64), nullable=False),
        sa.Column("context_classifier_version", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("semantic_hash", sa.String(length=64), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("evaluation_key", name="uq_feature_snapshots_evaluation_key"),
    )
    op.create_index("ix_feature_snapshots_match_as_of", "feature_snapshots", ["match_id", "as_of"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_feature_snapshots_match_as_of", table_name="feature_snapshots")
    op.drop_table("feature_snapshots")
