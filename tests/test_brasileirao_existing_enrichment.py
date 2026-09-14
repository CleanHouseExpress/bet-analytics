from pathlib import Path

from sqlalchemy import text

from apps.api.app.core.database import SessionLocal
from apps.api.app.services.brasileirao_existing_enrichment import (
    BrasileiraoExistingEnrichmentImporter,
)

COMPETITION = "Existing Enrichment Test League"
SEASON = "2098"


def _cleanup(db) -> None:
    competition_ids = db.execute(
        text("SELECT id FROM competitions WHERE name = :name"), {"name": COMPETITION}
    ).scalars().all()
    if competition_ids:
        db.execute(
            text(
                """
                DELETE FROM match_events WHERE match_id IN (
                    SELECT id FROM matches WHERE competition_id = ANY(:competition_ids)
                )
                """
            ),
            {"competition_ids": competition_ids},
        )
        db.execute(
            text(
                """
                DELETE FROM match_team_statistics WHERE match_id IN (
                    SELECT id FROM matches WHERE competition_id = ANY(:competition_ids)
                )
                """
            ),
            {"competition_ids": competition_ids},
        )
        db.execute(
            text("DELETE FROM matches WHERE competition_id = ANY(:competition_ids)"),
            {"competition_ids": competition_ids},
        )
        db.execute(
            text("DELETE FROM rounds WHERE season_id IN (SELECT id FROM seasons WHERE competition_id = ANY(:competition_ids))"),
            {"competition_ids": competition_ids},
        )
        db.execute(
            text("DELETE FROM seasons WHERE competition_id = ANY(:competition_ids)"),
            {"competition_ids": competition_ids},
        )
        db.execute(
            text("DELETE FROM competitions WHERE id = ANY(:competition_ids)"),
            {"competition_ids": competition_ids},
        )
    db.commit()


def _seed_match(db) -> int:
    now = "2098-01-01T00:00:00+00:00"
    competition_id = db.execute(
        text(
            """
            INSERT INTO competitions (
                name, short_name, country_code, competition_type,
                is_active, created_at, updated_at
            ) VALUES (:name, 'Test', 'BRA', 'league', true, :now, :now)
            RETURNING id
            """
        ),
        {"name": COMPETITION, "now": now},
    ).scalar_one()
    season_id = db.execute(
        text(
            """
            INSERT INTO seasons (
                competition_id, name, is_current, year_start, year_end,
                created_at, updated_at
            ) VALUES (:competition_id, :season, false, 2098, 2098, :now, :now)
            RETURNING id
            """
        ),
        {"competition_id": competition_id, "season": SEASON, "now": now},
    ).scalar_one()
    home_id = db.execute(
        text(
            """
            INSERT INTO teams (name, country_code, created_at, updated_at)
            VALUES ('Existing Home', 'BRA', :now, :now) RETURNING id
            """
        ),
        {"now": now},
    ).scalar_one()
    away_id = db.execute(
        text(
            """
            INSERT INTO teams (name, country_code, created_at, updated_at)
            VALUES ('Existing Away', 'BRA', :now, :now) RETURNING id
            """
        ),
        {"now": now},
    ).scalar_one()
    match_id = db.execute(
        text(
            """
            INSERT INTO matches (
                competition_id, season_id, home_team_id, away_team_id,
                kickoff_at, status, home_score, away_score, created_at, updated_at
            ) VALUES (
                :competition_id, :season_id, :home_id, :away_id,
                '2098-05-10T22:00:00+00:00', 'finished', 2, 1, :now, :now
            ) RETURNING id
            """
        ),
        {
            "competition_id": competition_id,
            "season_id": season_id,
            "home_id": home_id,
            "away_id": away_id,
            "now": now,
        },
    ).scalar_one()
    db.commit()
    return match_id


def test_existing_enrichment_updates_existing_match_without_creating_entities(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "existing.csv"
    csv_path.write_text(
        "competition,season,round,date,kickoff_time,home_team,away_team,home_score,away_score,"
        "ht_home_score,ht_away_score,winner,home_goals_minutes,away_goals_minutes,"
        "home_shots,away_shots,home_shots_on_target,away_shots_on_target,home_xg,away_xg,"
        "home_corners,away_corners,home_yellow_cards,away_yellow_cards,home_red_cards,away_red_cards,"
        "home_possession,away_possession,source_name,source_url,secondary_source_url,collected_at,confidence\n"
        f"{COMPETITION},{SEASON},10,2098-05-10,19:00,Existing Home,Existing Away,2,1,1,0,HOME,10;55,70,"
        "15,8,7,3,1.8,0.7,6,3,2,4,0,0,57,43,Test Source,https://example.test/a,,2098-05-11T00:00:00Z,high\n",
        encoding="utf-8",
    )

    with SessionLocal() as db:
        _cleanup(db)
        match_id = _seed_match(db)
        team_count_before = db.execute(text("SELECT count(*) FROM teams")).scalar_one()
        match_count_before = db.execute(text("SELECT count(*) FROM matches")).scalar_one()

        result = BrasileiraoExistingEnrichmentImporter(db).import_file(csv_path)

        assert result.rows_seen == 1
        assert result.matches_enriched == 1
        assert result.rows_skipped_missing_match == 0
        assert result.rows_skipped_ambiguous_match == 0
        assert result.statistics_upserted == 2
        assert result.ht_updates == 1
        assert result.goal_events_added == 3
        assert db.execute(text("SELECT count(*) FROM teams")).scalar_one() == team_count_before
        assert db.execute(text("SELECT count(*) FROM matches")).scalar_one() == match_count_before

        row = db.execute(
            text(
                """
                SELECT home_score_ht, away_score_ht
                FROM matches WHERE id = :match_id
                """
            ),
            {"match_id": match_id},
        ).one()
        assert row.home_score_ht == 1
        assert row.away_score_ht == 0

        _cleanup(db)
