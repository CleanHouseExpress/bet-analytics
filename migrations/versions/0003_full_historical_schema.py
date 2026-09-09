"""expand complete historical football schema

Revision ID: 0003_full_historical_schema
Revises: 0002_history_domain
Create Date: 2026-09-08
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0003_full_historical_schema"
down_revision: str | None = "0002_history_domain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "countries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("iso2", sa.String(2), nullable=True),
        sa.Column("iso3", sa.String(3), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("iso2", name="uq_countries_iso2"),
        sa.UniqueConstraint("iso3", name="uq_countries_iso3"),
    )

    op.create_table(
        "venues",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("country_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("city", sa.String(120), nullable=True),
        sa.Column("capacity", sa.Integer(), nullable=True),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["country_id"], ["countries.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "referees",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("country_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["country_id"], ["countries.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "coaches",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("country_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["country_id"], ["countries.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "players",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("country_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("display_name", sa.String(180), nullable=True),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("height_cm", sa.SmallInteger(), nullable=True),
        sa.Column("weight_kg", sa.SmallInteger(), nullable=True),
        sa.Column("preferred_foot", sa.String(16), nullable=True),
        sa.Column("primary_position", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["country_id"], ["countries.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_players_name", "players", ["name"], unique=False)

    op.add_column("competitions", sa.Column("country_id", sa.BigInteger(), nullable=True))
    op.add_column("competitions", sa.Column("short_name", sa.String(80), nullable=True))
    op.add_column("competitions", sa.Column("gender", sa.String(16), nullable=True))
    op.add_column("competitions", sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False))
    op.create_foreign_key("fk_competitions_country", "competitions", "countries", ["country_id"], ["id"], ondelete="SET NULL")

    op.add_column("teams", sa.Column("country_id", sa.BigInteger(), nullable=True))
    op.add_column("teams", sa.Column("code", sa.String(16), nullable=True))
    op.add_column("teams", sa.Column("founded_year", sa.SmallInteger(), nullable=True))
    op.add_column("teams", sa.Column("logo_url", sa.Text(), nullable=True))
    op.add_column("teams", sa.Column("venue_id", sa.BigInteger(), nullable=True))
    op.add_column("teams", sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False))
    op.create_foreign_key("fk_teams_country", "teams", "countries", ["country_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_teams_venue", "teams", "venues", ["venue_id"], ["id"], ondelete="SET NULL")

    op.create_table(
        "team_aliases",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("alias", sa.String(180), nullable=False),
        sa.Column("normalized_alias", sa.String(180), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_alias", name="uq_team_aliases_normalized"),
    )

    op.add_column("seasons", sa.Column("year_start", sa.SmallInteger(), nullable=True))
    op.add_column("seasons", sa.Column("year_end", sa.SmallInteger(), nullable=True))

    op.create_table(
        "stages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("season_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("stage_type", sa.String(40), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_stages_season", "stages", ["season_id", "sort_order"], unique=False)

    op.create_table(
        "rounds",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("season_id", sa.BigInteger(), nullable=False),
        sa.Column("stage_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stage_id"], ["stages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rounds_season_number", "rounds", ["season_id", "round_number"], unique=False)

    op.create_table(
        "competition_groups",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("season_id", sa.BigInteger(), nullable=False),
        sa.Column("stage_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stage_id"], ["stages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.add_column("matches", sa.Column("stage_id", sa.BigInteger(), nullable=True))
    op.add_column("matches", sa.Column("round_id", sa.BigInteger(), nullable=True))
    op.add_column("matches", sa.Column("group_id", sa.BigInteger(), nullable=True))
    op.add_column("matches", sa.Column("venue_id", sa.BigInteger(), nullable=True))
    op.add_column("matches", sa.Column("referee_id", sa.BigInteger(), nullable=True))
    op.add_column("matches", sa.Column("minute", sa.SmallInteger(), nullable=True))
    op.add_column("matches", sa.Column("extra_minute", sa.SmallInteger(), nullable=True))
    op.add_column("matches", sa.Column("home_score_ht", sa.Integer(), nullable=True))
    op.add_column("matches", sa.Column("away_score_ht", sa.Integer(), nullable=True))
    op.add_column("matches", sa.Column("home_score_et", sa.Integer(), nullable=True))
    op.add_column("matches", sa.Column("away_score_et", sa.Integer(), nullable=True))
    op.add_column("matches", sa.Column("home_score_penalties", sa.Integer(), nullable=True))
    op.add_column("matches", sa.Column("away_score_penalties", sa.Integer(), nullable=True))
    op.add_column("matches", sa.Column("winner_team_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_matches_stage", "matches", "stages", ["stage_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_matches_round", "matches", "rounds", ["round_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_matches_group", "matches", "competition_groups", ["group_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_matches_venue", "matches", "venues", ["venue_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_matches_referee", "matches", "referees", ["referee_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_matches_winner_team", "matches", "teams", ["winner_team_id"], ["id"], ondelete="SET NULL")

    op.create_table(
        "match_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("match_id", sa.BigInteger(), nullable=False),
        sa.Column("team_id", sa.BigInteger(), nullable=True),
        sa.Column("player_id", sa.BigInteger(), nullable=True),
        sa.Column("related_player_id", sa.BigInteger(), nullable=True),
        sa.Column("period", sa.String(24), nullable=True),
        sa.Column("minute", sa.SmallInteger(), nullable=True),
        sa.Column("extra_minute", sa.SmallInteger(), nullable=True),
        sa.Column("second", sa.SmallInteger(), nullable=True),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("event_subtype", sa.String(80), nullable=True),
        sa.Column("value", sa.String(160), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["related_player_id"], ["players.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_match_events_match_timeline", "match_events", ["match_id", "minute", "extra_minute", "second"], unique=False)
    op.create_index("ix_match_events_type", "match_events", ["event_type"], unique=False)

    op.create_table(
        "match_team_statistics",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("match_id", sa.BigInteger(), nullable=False),
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("period", sa.String(24), nullable=False, server_default="FULL_TIME"),
        sa.Column("possession", sa.Numeric(5, 2), nullable=True),
        sa.Column("shots", sa.Integer(), nullable=True),
        sa.Column("shots_on_target", sa.Integer(), nullable=True),
        sa.Column("shots_off_target", sa.Integer(), nullable=True),
        sa.Column("blocked_shots", sa.Integer(), nullable=True),
        sa.Column("xg", sa.Numeric(8, 4), nullable=True),
        sa.Column("xgot", sa.Numeric(8, 4), nullable=True),
        sa.Column("corners", sa.Integer(), nullable=True),
        sa.Column("offsides", sa.Integer(), nullable=True),
        sa.Column("fouls", sa.Integer(), nullable=True),
        sa.Column("yellow_cards", sa.Integer(), nullable=True),
        sa.Column("red_cards", sa.Integer(), nullable=True),
        sa.Column("passes", sa.Integer(), nullable=True),
        sa.Column("passes_completed", sa.Integer(), nullable=True),
        sa.Column("attacks", sa.Integer(), nullable=True),
        sa.Column("dangerous_attacks", sa.Integer(), nullable=True),
        sa.Column("crosses", sa.Integer(), nullable=True),
        sa.Column("saves", sa.Integer(), nullable=True),
        sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_id", "team_id", "period", name="uq_match_team_statistics_period"),
    )

    op.create_table(
        "match_stat_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("match_id", sa.BigInteger(), nullable=False),
        sa.Column("minute", sa.SmallInteger(), nullable=False),
        sa.Column("extra_minute", sa.SmallInteger(), nullable=True),
        sa.Column("home_score", sa.Integer(), nullable=True),
        sa.Column("away_score", sa.Integer(), nullable=True),
        sa.Column("home_xg", sa.Numeric(8, 4), nullable=True),
        sa.Column("away_xg", sa.Numeric(8, 4), nullable=True),
        sa.Column("home_shots", sa.Integer(), nullable=True),
        sa.Column("away_shots", sa.Integer(), nullable=True),
        sa.Column("home_sot", sa.Integer(), nullable=True),
        sa.Column("away_sot", sa.Integer(), nullable=True),
        sa.Column("home_corners", sa.Integer(), nullable=True),
        sa.Column("away_corners", sa.Integer(), nullable=True),
        sa.Column("home_cards", sa.Integer(), nullable=True),
        sa.Column("away_cards", sa.Integer(), nullable=True),
        sa.Column("home_possession", sa.Numeric(5, 2), nullable=True),
        sa.Column("away_possession", sa.Numeric(5, 2), nullable=True),
        sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_match_stat_snapshots_timeline", "match_stat_snapshots", ["match_id", "minute", "captured_at"], unique=False)

    op.create_table(
        "match_formations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("match_id", sa.BigInteger(), nullable=False),
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("coach_id", sa.BigInteger(), nullable=True),
        sa.Column("formation", sa.String(32), nullable=True),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["coach_id"], ["coaches.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_id", "team_id", name="uq_match_formations_team"),
    )

    op.create_table(
        "match_squads",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("match_id", sa.BigInteger(), nullable=False),
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("shirt_number", sa.SmallInteger(), nullable=True),
        sa.Column("position", sa.String(32), nullable=True),
        sa.Column("status", sa.String(24), nullable=True),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_id", "team_id", "player_id", name="uq_match_squads_player"),
    )

    op.create_table(
        "match_lineups",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("match_id", sa.BigInteger(), nullable=False),
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("is_starting", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("position", sa.String(32), nullable=True),
        sa.Column("formation_position", sa.String(32), nullable=True),
        sa.Column("shirt_number", sa.SmallInteger(), nullable=True),
        sa.Column("is_captain", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_id", "team_id", "player_id", name="uq_match_lineups_player"),
    )

    op.create_table(
        "player_team_memberships",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("season_id", sa.BigInteger(), nullable=True),
        sa.Column("from_date", sa.Date(), nullable=True),
        sa.Column("to_date", sa.Date(), nullable=True),
        sa.Column("shirt_number", sa.SmallInteger(), nullable=True),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_player_team_memberships_history", "player_team_memberships", ["player_id", "from_date", "to_date"], unique=False)

    op.create_table(
        "player_match_statistics",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("match_id", sa.BigInteger(), nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("minutes_played", sa.SmallInteger(), nullable=True),
        sa.Column("goals", sa.SmallInteger(), nullable=True),
        sa.Column("assists", sa.SmallInteger(), nullable=True),
        sa.Column("shots", sa.SmallInteger(), nullable=True),
        sa.Column("shots_on_target", sa.SmallInteger(), nullable=True),
        sa.Column("passes", sa.Integer(), nullable=True),
        sa.Column("key_passes", sa.SmallInteger(), nullable=True),
        sa.Column("tackles", sa.SmallInteger(), nullable=True),
        sa.Column("interceptions", sa.SmallInteger(), nullable=True),
        sa.Column("fouls", sa.SmallInteger(), nullable=True),
        sa.Column("fouls_drawn", sa.SmallInteger(), nullable=True),
        sa.Column("yellow_cards", sa.SmallInteger(), nullable=True),
        sa.Column("red_cards", sa.SmallInteger(), nullable=True),
        sa.Column("xg", sa.Numeric(8, 4), nullable=True),
        sa.Column("xa", sa.Numeric(8, 4), nullable=True),
        sa.Column("rating", sa.Numeric(5, 2), nullable=True),
        sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_id", "player_id", name="uq_player_match_statistics"),
    )

    op.create_table(
        "player_absences",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("absence_type", sa.String(40), nullable=False),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("expected_end_date", sa.Date(), nullable=True),
        sa.Column("actual_end_date", sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "standings_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("competition_id", sa.BigInteger(), nullable=False),
        sa.Column("season_id", sa.BigInteger(), nullable=False),
        sa.Column("round_id", sa.BigInteger(), nullable=True),
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("played", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("wins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("draws", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("losses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("goals_for", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("goals_against", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["competition_id"], ["competitions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["round_id"], ["rounds.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_standings_snapshots_lookup", "standings_snapshots", ["season_id", "team_id", "snapshot_at"], unique=False)

    op.create_table(
        "bookmakers",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("country_code", sa.String(3), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_bookmakers_name"),
    )

    op.create_table(
        "markets",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("category", sa.String(60), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_markets_code"),
    )

    op.create_table(
        "odds_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("match_id", sa.BigInteger(), nullable=False),
        sa.Column("bookmaker_id", sa.BigInteger(), nullable=False),
        sa.Column("market_id", sa.BigInteger(), nullable=False),
        sa.Column("selection", sa.String(120), nullable=False),
        sa.Column("line", sa.Numeric(10, 4), nullable=True),
        sa.Column("odd", sa.Numeric(10, 4), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["bookmaker_id"], ["bookmakers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_odds_snapshots_market_time", "odds_snapshots", ["match_id", "market_id", "captured_at"], unique=False)

    op.create_table(
        "match_features",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("match_id", sa.BigInteger(), nullable=False),
        sa.Column("feature_version", sa.String(40), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("features", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_id", "feature_version", name="uq_match_features_version"),
    )

    op.create_table(
        "provider_raw_payloads",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(60), nullable=False),
        sa.Column("resource_type", sa.String(80), nullable=False),
        sa.Column("external_id", sa.String(120), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_provider_raw_payloads_lookup", "provider_raw_payloads", ["provider", "resource_type", "external_id", "fetched_at"], unique=False)

    op.create_table(
        "external_entity_mappings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("entity_type", sa.String(60), nullable=False),
        sa.Column("internal_id", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.String(60), nullable=False),
        sa.Column("external_id", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "entity_type", "external_id", name="uq_external_entity_provider_external"),
        sa.UniqueConstraint("provider", "entity_type", "internal_id", name="uq_external_entity_provider_internal"),
    )

    op.create_table(
        "provider_sync_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(60), nullable=False),
        sa.Column("resource", sa.String(80), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("records_received", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "provider_sync_cursors",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(60), nullable=False),
        sa.Column("resource", sa.String(80), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cursor", sa.String(255), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "resource", name="uq_provider_sync_cursors"),
    )


def downgrade() -> None:
    op.drop_table("provider_sync_cursors")
    op.drop_table("provider_sync_runs")
    op.drop_table("external_entity_mappings")
    op.drop_index("ix_provider_raw_payloads_lookup", table_name="provider_raw_payloads")
    op.drop_table("provider_raw_payloads")
    op.drop_table("match_features")
    op.drop_index("ix_odds_snapshots_market_time", table_name="odds_snapshots")
    op.drop_table("odds_snapshots")
    op.drop_table("markets")
    op.drop_table("bookmakers")
    op.drop_index("ix_standings_snapshots_lookup", table_name="standings_snapshots")
    op.drop_table("standings_snapshots")
    op.drop_table("player_absences")
    op.drop_table("player_match_statistics")
    op.drop_index("ix_player_team_memberships_history", table_name="player_team_memberships")
    op.drop_table("player_team_memberships")
    op.drop_table("match_lineups")
    op.drop_table("match_squads")
    op.drop_table("match_formations")
    op.drop_index("ix_match_stat_snapshots_timeline", table_name="match_stat_snapshots")
    op.drop_table("match_stat_snapshots")
    op.drop_table("match_team_statistics")
    op.drop_index("ix_match_events_type", table_name="match_events")
    op.drop_index("ix_match_events_match_timeline", table_name="match_events")
    op.drop_table("match_events")

    op.drop_constraint("fk_matches_winner_team", "matches", type_="foreignkey")
    op.drop_constraint("fk_matches_referee", "matches", type_="foreignkey")
    op.drop_constraint("fk_matches_venue", "matches", type_="foreignkey")
    op.drop_constraint("fk_matches_group", "matches", type_="foreignkey")
    op.drop_constraint("fk_matches_round", "matches", type_="foreignkey")
    op.drop_constraint("fk_matches_stage", "matches", type_="foreignkey")
    for column in ["winner_team_id", "away_score_penalties", "home_score_penalties", "away_score_et", "home_score_et", "away_score_ht", "home_score_ht", "extra_minute", "minute", "referee_id", "venue_id", "group_id", "round_id", "stage_id"]:
        op.drop_column("matches", column)

    op.drop_table("competition_groups")
    op.drop_index("ix_rounds_season_number", table_name="rounds")
    op.drop_table("rounds")
    op.drop_index("ix_stages_season", table_name="stages")
    op.drop_table("stages")

    op.drop_column("seasons", "year_end")
    op.drop_column("seasons", "year_start")
    op.drop_table("team_aliases")

    op.drop_constraint("fk_teams_venue", "teams", type_="foreignkey")
    op.drop_constraint("fk_teams_country", "teams", type_="foreignkey")
    for column in ["is_active", "venue_id", "logo_url", "founded_year", "code", "country_id"]:
        op.drop_column("teams", column)

    op.drop_constraint("fk_competitions_country", "competitions", type_="foreignkey")
    for column in ["is_active", "gender", "short_name", "country_id"]:
        op.drop_column("competitions", column)

    op.drop_index("ix_players_name", table_name="players")
    op.drop_table("players")
    op.drop_table("coaches")
    op.drop_table("referees")
    op.drop_table("venues")
    op.drop_table("countries")
