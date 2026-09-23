"""walk-forward backtest runs and evaluations

Revision ID: 0014_walk_forward_backtest
Revises: 0013_decision_journal
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014_walk_forward_backtest"
down_revision: str | None = "0013_decision_journal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("semantic_hash", sa.String(64), nullable=False),
        sa.Column("data_fingerprint", sa.String(64), nullable=False),
        sa.Column("backtest_version", sa.String(64), nullable=False),
        sa.Column("competition_name", sa.String(160), nullable=False),
        sa.Column("seasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("feature_engine_version", sa.String(64), nullable=False),
        sa.Column("model_name", sa.String(64), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("market_engine_version", sa.String(64), nullable=False),
        sa.Column("value_engine_version", sa.String(64), nullable=False),
        sa.Column("risk_engine_version", sa.String(64), nullable=False),
        sa.Column("decision_journal_version", sa.String(64), nullable=False),
        sa.Column("initial_bankroll", sa.Float(), nullable=False),
        sa.Column("ending_bankroll", sa.Float(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("segments", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("equity_curve", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", name="uq_backtest_runs_run_id"),
        sa.UniqueConstraint(
            "semantic_hash",
            name="uq_backtest_runs_semantic_hash",
        ),
    )
    op.create_index(
        "ix_backtest_runs_generated_at",
        "backtest_runs",
        ["generated_at"],
    )

    op.create_table(
        "backtest_evaluations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("match_id", sa.BigInteger(), nullable=False),
        sa.Column("season", sa.String(16), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("kickoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("market", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("p_model", sa.Float(), nullable=True),
        sa.Column("p_cons", sa.Float(), nullable=True),
        sa.Column("p_break_even", sa.Float(), nullable=True),
        sa.Column("edge_pp", sa.Float(), nullable=True),
        sa.Column("ev_cons", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("baseline_probability", sa.Float(), nullable=True),
        sa.Column("actual_outcome", sa.Boolean(), nullable=False),
        sa.Column("model_side", sa.String(16), nullable=True),
        sa.Column("market_odd", sa.Float(), nullable=True),
        sa.Column("odd_source", sa.String(160), nullable=True),
        sa.Column("odd_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("value_decision", sa.String(32), nullable=True),
        sa.Column("risk_decision", sa.String(32), nullable=True),
        sa.Column("stake_units", sa.Float(), nullable=False),
        sa.Column("stake_value", sa.Float(), nullable=False),
        sa.Column("settlement_result", sa.String(16), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("profit_loss", sa.Float(), nullable=True),
        sa.Column("journal_entry_id", sa.String(64), nullable=True),
        sa.Column("feature_semantic_hash", sa.String(64), nullable=True),
        sa.Column("poisson_semantic_hash", sa.String(64), nullable=True),
        sa.Column(
            "market_probability_semantic_hash",
            sa.String(64),
            nullable=True,
        ),
        sa.Column("value_semantic_hash", sa.String(64), nullable=True),
        sa.Column("risk_semantic_hash", sa.String(64), nullable=True),
        sa.Column(
            "feature_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("block_reason", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["backtest_runs.run_id"],
            name="fk_backtest_evaluations_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["matches.id"],
            name="fk_backtest_evaluations_match",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "run_id",
            "match_id",
            "market",
            name="uq_backtest_evaluation_identity",
        ),
    )
    op.create_index(
        "ix_backtest_evaluations_run_status",
        "backtest_evaluations",
        ["run_id", "status"],
    )
    op.create_index(
        "ix_backtest_evaluations_market_season",
        "backtest_evaluations",
        ["market", "season"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_backtest_evaluations_market_season",
        table_name="backtest_evaluations",
    )
    op.drop_index(
        "ix_backtest_evaluations_run_status",
        table_name="backtest_evaluations",
    )
    op.drop_table("backtest_evaluations")
    op.drop_index(
        "ix_backtest_runs_generated_at",
        table_name="backtest_runs",
    )
    op.drop_table("backtest_runs")
