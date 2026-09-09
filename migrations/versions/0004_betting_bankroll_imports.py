"""add betting, bankroll and spreadsheet import schema

Revision ID: 0004_betting_bankroll_imports
Revises: 0003_full_historical_schema
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_betting_bankroll_imports"
down_revision: str | None = "0003_full_historical_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "betting_accounts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("bookmaker_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="BRL"),
        sa.Column("initial_balance", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["bookmaker_id"], ["bookmakers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_betting_accounts_name"),
    )

    op.create_table(
        "strategies",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("version", sa.String(40), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", "version", name="uq_strategies_code_version"),
    )

    op.create_table(
        "bets",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("match_id", sa.BigInteger(), nullable=True),
        sa.Column("bookmaker_id", sa.BigInteger(), nullable=True),
        sa.Column("external_bet_id", sa.String(120), nullable=True),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bet_type", sa.String(24), nullable=False, server_default="single"),
        sa.Column("stake", sa.Numeric(14, 2), nullable=False),
        sa.Column("total_odd", sa.Numeric(12, 4), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="settled"),
        sa.Column("result", sa.String(16), nullable=True),
        sa.Column("return_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("profit_loss", sa.Numeric(14, 2), nullable=True),
        sa.Column("source", sa.String(40), nullable=False, server_default="manual"),
        sa.Column("source_fingerprint", sa.String(64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("result IS NULL OR result IN ('Green', 'Red')", name="ck_bets_result_green_red"),
        sa.CheckConstraint("stake >= 0", name="ck_bets_stake_non_negative"),
        sa.ForeignKeyConstraint(["account_id"], ["betting_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["bookmaker_id"], ["bookmakers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "external_bet_id", name="uq_bets_account_external_id"),
        sa.UniqueConstraint("source_fingerprint", name="uq_bets_source_fingerprint"),
    )
    op.create_index("ix_bets_placed_at", "bets", ["placed_at"], unique=False)
    op.create_index("ix_bets_match", "bets", ["match_id", "placed_at"], unique=False)

    op.create_table(
        "bet_selections",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("bet_id", sa.BigInteger(), nullable=False),
        sa.Column("match_id", sa.BigInteger(), nullable=True),
        sa.Column("market", sa.String(120), nullable=False),
        sa.Column("selection", sa.String(180), nullable=False),
        sa.Column("line", sa.Numeric(12, 4), nullable=True),
        sa.Column("odd", sa.Numeric(12, 4), nullable=True),
        sa.Column("is_live", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("minute_placed", sa.SmallInteger(), nullable=True),
        sa.Column("score_when_placed", sa.String(20), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.ForeignKeyConstraint(["bet_id"], ["bets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bet_selections_bet", "bet_selections", ["bet_id"], unique=False)

    op.create_table(
        "bet_settlements",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("bet_id", sa.BigInteger(), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("return_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("profit_loss", sa.Numeric(14, 2), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint("result IN ('Green', 'Red')", name="ck_bet_settlements_green_red"),
        sa.ForeignKeyConstraint(["bet_id"], ["bets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bet_id", name="uq_bet_settlements_bet"),
    )

    op.create_table(
        "bankroll_movements",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("bet_id", sa.BigInteger(), nullable=True),
        sa.Column("movement_type", sa.String(32), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("balance_after", sa.Numeric(14, 2), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(40), nullable=False, server_default="manual"),
        sa.Column("source_fingerprint", sa.String(64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["betting_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["bet_id"], ["bets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_fingerprint", name="uq_bankroll_movements_source_fingerprint"),
    )
    op.create_index("ix_bankroll_movements_time", "bankroll_movements", ["account_id", "occurred_at"], unique=False)

    op.create_table(
        "daily_bankroll_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("starting_balance", sa.Numeric(14, 2), nullable=True),
        sa.Column("ending_balance", sa.Numeric(14, 2), nullable=True),
        sa.Column("deposits", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("withdrawals", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("stake_total", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("return_total", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("profit_loss", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("project_balance", sa.Numeric(14, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["betting_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "snapshot_date", name="uq_daily_bankroll_account_date"),
    )

    op.create_table(
        "bet_strategy_assignments",
        sa.Column("bet_id", sa.BigInteger(), nullable=False),
        sa.Column("strategy_id", sa.BigInteger(), nullable=False),
        sa.Column("confidence", sa.Numeric(6, 4), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.ForeignKeyConstraint(["bet_id"], ["bets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("bet_id", "strategy_id"),
    )

    op.create_table(
        "import_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_type", sa.String(40), nullable=False, server_default="spreadsheet"),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rows_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_imported", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_import_runs_file_hash", "import_runs", ["file_sha256", "started_at"], unique=False)

    op.create_table(
        "import_rows",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("import_run_id", sa.BigInteger(), nullable=False),
        sa.Column("sheet_name", sa.String(160), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("row_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("entity_type", sa.String(60), nullable=True),
        sa.Column("entity_id", sa.BigInteger(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["import_run_id"], ["import_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("row_fingerprint", name="uq_import_rows_fingerprint"),
    )
    op.create_index("ix_import_rows_run_status", "import_rows", ["import_run_id", "status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_import_rows_run_status", table_name="import_rows")
    op.drop_table("import_rows")
    op.drop_index("ix_import_runs_file_hash", table_name="import_runs")
    op.drop_table("import_runs")
    op.drop_table("bet_strategy_assignments")
    op.drop_table("daily_bankroll_snapshots")
    op.drop_index("ix_bankroll_movements_time", table_name="bankroll_movements")
    op.drop_table("bankroll_movements")
    op.drop_table("bet_settlements")
    op.drop_index("ix_bet_selections_bet", table_name="bet_selections")
    op.drop_table("bet_selections")
    op.drop_index("ix_bets_match", table_name="bets")
    op.drop_index("ix_bets_placed_at", table_name="bets")
    op.drop_table("bets")
    op.drop_table("strategies")
    op.drop_table("betting_accounts")
