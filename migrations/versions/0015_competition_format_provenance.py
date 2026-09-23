"""add explicit competition format provenance

Revision ID: 0015_comp_format_provenance
Revises: 0014_walk_forward_backtest
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_comp_format_provenance"
down_revision: str | None = "0014_walk_forward_backtest"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "competitions",
        sa.Column("competition_type_source", sa.String(length=120), nullable=True),
    )

    # These are canonical, curated competition identities owned by the project.
    # Legacy generic-provider rows are deliberately NOT trusted/backfilled.
    op.execute(
        """
        UPDATE competitions
        SET competition_type_source = 'curated:brazilian-leagues-v1'
        WHERE competition_type = 'league'
          AND name IN (
              'Brasileirão Série A',
              'Brasileirão Série B',
              'Brasileirão Série C',
              'Brasileirão Série D'
          )
        """
    )


def downgrade() -> None:
    op.drop_column("competitions", "competition_type_source")
