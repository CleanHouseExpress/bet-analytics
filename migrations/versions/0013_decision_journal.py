"""decision journal entries

Revision ID: 0013_decision_journal
Revises: 0012_risk_assess_snapshots
"""

import sqlalchemy as sa
from alembic import op

revision = "0013_decision_journal"
down_revision = "0012_risk_assess_snapshots"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "decision_journal_entries",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("journal_entry_id", sa.String(64), nullable=False),
        sa.Column("semantic_hash", sa.String(64), nullable=False),
        sa.Column("match_id", sa.BigInteger(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("market", sa.String(64), nullable=False),
        sa.Column("analysis_type", sa.String(32), nullable=False),
        sa.Column("decision_journal_version", sa.String(64), nullable=False),
        sa.Column("feature_engine_version", sa.String(64), nullable=False),
        sa.Column("model_name", sa.String(64), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("market_engine_version", sa.String(64), nullable=False),
        sa.Column("value_engine_version", sa.String(64), nullable=False),
        sa.Column("risk_engine_version", sa.String(64), nullable=False),
        sa.Column("feature_semantic_hash", sa.String(64), nullable=False),
        sa.Column("poisson_semantic_hash", sa.String(64), nullable=False),
        sa.Column("market_probability_semantic_hash", sa.String(64), nullable=False),
        sa.Column("value_semantic_hash", sa.String(64), nullable=False),
        sa.Column("risk_semantic_hash", sa.String(64), nullable=False),
        sa.Column("value_decision", sa.String(32), nullable=False),
        sa.Column("risk_decision", sa.String(32), nullable=False),
        sa.Column("stake_units", sa.Float(), nullable=False),
        sa.Column("stake_value", sa.Float(), nullable=False),
        sa.Column("exposure_known", sa.Boolean(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint("journal_entry_id", name="uq_decision_journal_entry_id"),
        sa.UniqueConstraint("semantic_hash", name="uq_decision_journal_semantic_hash"),
    )
    op.create_index(
        "ix_decision_journal_match_asof",
        "decision_journal_entries",
        ["match_id", "as_of"],
    )
    op.create_table(
        "decision_settlements",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("journal_entry_id", sa.String(64), nullable=False),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("profit_loss", sa.Float(), nullable=False),
        sa.Column("closing_odd", sa.Float(), nullable=True),
        sa.Column("clv", sa.Float(), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["journal_entry_id"],
            ["decision_journal_entries.journal_entry_id"],
            name="fk_decision_settlement_journal_entry",
        ),
        sa.UniqueConstraint(
            "journal_entry_id",
            name="uq_decision_settlement_journal_entry",
        ),
    )


def downgrade():
    op.drop_table("decision_settlements")
    op.drop_index(
        "ix_decision_journal_match_asof",
        table_name="decision_journal_entries",
    )
    op.drop_table("decision_journal_entries")
