"""persist immutable match context snapshots

Revision ID: 0006_match_context_snapshots
Revises: 0005_fixture_sync_tracking
Create Date: 2026-09-15
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0006_match_context_snapshots"
down_revision: str | None = "0005_fixture_sync_tracking"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "match_context_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("evaluation_key", sa.String(160), nullable=False),
        sa.Column("match_id", sa.BigInteger(), nullable=False),
        sa.Column("competition_id", sa.BigInteger(), nullable=False),
        sa.Column("season_id", sa.BigInteger(), nullable=False),
        sa.Column("competition_format", sa.String(40), nullable=False),
        sa.Column("analysis_type", sa.String(24), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("classified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("classifier_version", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["competition_id"], ["competitions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("evaluation_key", name="uq_match_context_snapshots_evaluation_key"),
    )
    op.create_index(
        "ix_match_context_snapshots_match_as_of",
        "match_context_snapshots",
        ["match_id", "as_of"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_match_context_snapshots_match_as_of", table_name="match_context_snapshots")
    op.drop_table("match_context_snapshots")
