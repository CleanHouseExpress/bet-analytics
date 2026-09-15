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
    op.add_column("matches", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))

    # Conservative backfill: use persisted provider/sync evidence. Never infer a
    # completion time from kickoff + an assumed match duration.
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

    # Keep the temporal evidence invariant at the database boundary, including
    # ingestion paths that use raw SQL. The first observation of a final score is
    # immutable; later syncs must not move it forward or backward.
    op.execute(
        """
        CREATE FUNCTION set_match_finished_at() RETURNS trigger AS $$
        BEGIN
            IF NEW.status = 'finished'
               AND NEW.home_score IS NOT NULL
               AND NEW.away_score IS NOT NULL
               AND NEW.finished_at IS NULL THEN
                NEW.finished_at := COALESCE(NEW.provider_updated_at, NEW.last_synced_at, CURRENT_TIMESTAMP);
            END IF;
            IF TG_OP = 'UPDATE' AND OLD.finished_at IS NOT NULL THEN
                NEW.finished_at := OLD.finished_at;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_matches_finished_at
        BEFORE INSERT OR UPDATE ON matches
        FOR EACH ROW EXECUTE FUNCTION set_match_finished_at();
        """
    )
    op.create_index("ix_matches_finished_at", "matches", ["finished_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_matches_finished_at", table_name="matches")
    op.execute("DROP TRIGGER IF EXISTS trg_matches_finished_at ON matches")
    op.execute("DROP FUNCTION IF EXISTS set_match_finished_at()")
    op.drop_column("matches", "finished_at")
