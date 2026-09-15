"""persist match finished evidence timestamp

Revision ID: 0008_match_finished_at
Revises: 0007_feature_snapshots
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_match_finished_at"
down_revision: str | None = "0007_feature_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "matches",
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Conservative historical backfill: this is the earliest timestamp already
    # persisted by our ingestion pipeline that proves we observed the final result.
    # Never infer completion from kickoff duration.
    op.execute(
        """
        UPDATE matches
        SET finished_at = COALESCE(provider_updated_at, last_synced_at, updated_at)
        WHERE status = 'finished'
          AND home_score IS NOT NULL
          AND away_score IS NOT NULL
          AND finished_at IS NULL
        """
    )
    op.create_index(
        "ix_matches_finished_at",
        "matches",
        ["finished_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_matches_finished_at", table_name="matches")
    op.drop_column("matches", "finished_at")
