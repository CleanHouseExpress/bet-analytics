"""fixture sync tracking

Revision ID: 0005_fixture_sync_tracking
Revises: 0004_betting_bankroll_imports
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_fixture_sync_tracking"
down_revision: str | None = "0004_betting_bankroll_imports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "matches",
        sa.Column("provider_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "matches",
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_matches_status_kickoff",
        "matches",
        ["status", "kickoff_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_matches_status_kickoff", table_name="matches")
    op.drop_column("matches", "last_synced_at")
    op.drop_column("matches", "provider_updated_at")
