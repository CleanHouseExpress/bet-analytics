from sqlalchemy import inspect

from apps.api.app.core.database import engine


EXPECTED_TABLES = {
    "countries",
    "competitions",
    "seasons",
    "stages",
    "rounds",
    "competition_groups",
    "teams",
    "team_aliases",
    "venues",
    "referees",
    "coaches",
    "players",
    "matches",
    "match_events",
    "match_team_statistics",
    "match_stat_snapshots",
    "match_formations",
    "match_squads",
    "match_lineups",
    "player_team_memberships",
    "player_match_statistics",
    "player_absences",
    "standings_snapshots",
    "bookmakers",
    "markets",
    "odds_snapshots",
    "match_features",
    "provider_raw_payloads",
    "external_entity_mappings",
    "provider_sync_runs",
    "provider_sync_cursors",
}


def test_full_historical_schema_exists() -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    assert EXPECTED_TABLES.issubset(tables)


def test_matches_has_historical_context_columns() -> None:
    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("matches")}

    assert {
        "stage_id",
        "round_id",
        "group_id",
        "venue_id",
        "referee_id",
        "home_score_ht",
        "away_score_ht",
        "winner_team_id",
    }.issubset(columns)
