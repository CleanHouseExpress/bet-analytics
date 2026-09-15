"""persist immutable poisson model snapshots

Revision ID: 0009_poisson_model_snapshots
Revises: 0008_match_finished_at
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0009_poisson_model_snapshots"
down_revision = "0008_match_finished_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("evaluation_key", sa.String(length=200), nullable=False),
        sa.Column("match_id", sa.BigInteger(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model_name", sa.String(length=64), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column("feature_engine_version", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("semantic_hash", sa.String(length=64), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["matches.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "evaluation_key",
            name="uq_model_snapshots_evaluation_key",
        ),
    )

    op.create_index(
        "ix_model_snapshots_match_as_of",
        "model_snapshots",
        ["match_id", "as_of"],
        unique=False,
    )

    op.create_index(
        "ix_model_snapshots_model_version",
        "model_snapshots",
        ["model_name", "model_version"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_model_snapshots_model_version",
        table_name="model_snapshots",
    )
    op.drop_index(
        "ix_model_snapshots_match_as_of",
        table_name="model_snapshots",
    )
    op.drop_table("model_snapshots")
