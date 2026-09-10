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
            text("DELETE FROM competitions WHERE id = ANY(:ids)"),
            {"ids": competition_ids},
        )
    db.execute(
        text("DELETE FROM teams WHERE name IN ('Existing Home FC', 'Existing Away FC')")
    )
    db.commit()


def _seed(db) -> tuple[int, int, int]:
    now = "2098-01-01T00:00:00+00:00"
    competition_id = db.execute(
        text(
            """
            INSERT INTO competitions (
                name, country_code, competition_type, created_at, updated_at
            ) VALUES (:name, 'BRA', 'league', :now, :now)
            RETURNING id
            """
        ),
        {"name": COMPETITION, "now": now},
    ).scalar_one()
    season_id = db.execute(
        text(
            """
            INSERT INTO seasons (
                competition_id, name, start_date, end_date, is_current,
                created_at, updated_at
            ) VALUES (
                :competition_id, :name, '2098-01-01', '2098-12-31', false,
                :now, :now
            ) RETURNING id
            """
        ),
        {"competition_id": competition_id, "name": SEASON, "now": now},
    ).scalar_one()
    home_id = db.execute(
        text(
            """
            INSERT INTO teams (name, country_code, created_at, updated_at)
            VALUES ('Existing Home FC', 'BRA', :now, :now)
            RETURNING id
            """
        ),
        {"now": now},
    ).scalar_one()
    away_id = db.execute(
        text(
            """
            INSERT INTO teams (name, country_code, created_at, updated_at)
            VALUES ('Existing Away FC', 'BRA', :now, :now)
            RETURNING id
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
    db.execute(
        text(
            """
            INSERT INTO match_events (
                match_id, team_id, period, minute, event_type, created_at
            ) VALUES (:match_id, :team_id, 'FIRST_HALF', 12, 'goal', :now)
            """
        ),
        {"match_id": match_id, "team_id": home_id, "now": now},
    )
    db.commit()
    return match_id, home_id, away_id


def _csv(path: Path) -> None:
    path.write_text(
        "competition,season,round,date,kickoff_time,home_team,away_team,home_score,"
        "away_score,ht_home_score,ht_away_score,winner,home_goals_minutes,"
        "away_goals_minutes,home_shots,away_shots,home_shots_on_target,"
        "away_shots_on_target,home_xg,away_xg,home_corners,away_corners,"
        "home_yellow_cards,away_yellow_cards,home_red_cards,away_red_cards,"
        "home_possession,away_possession,source_name,source_url,secondary_source_url,"
        "collected_at,confidence\n"
        f"{COMPETITION},{SEASON},1,2098-05-10,19:00,Existing Home FC,Existing Away FC,"
        "2,1,1,0,HOME,12|78,65,14,9,6,3,1.80,0.75,5,2,2,4,0,0,58,42,"
        "Public Stats,https://example.com/match,,2098-05-11T00:00:00Z,MEDIUM\n",
        encoding="utf-8",
    )


def test_existing_only_enriches_without_creating_or_duplicating_goals(tmp_path: Path) -> None:
    csv_path = tmp_path / "existing.csv"
    _csv(csv_path)

    with SessionLocal() as db:
        _cleanup(db)
        match_id, home_id, away_id = _seed(db)
        importer = BrasileiraoExistingEnrichmentImporter(db)

        first = importer.import_file(csv_path)
        assert first.rows_seen == 1
        assert first.matches_enriched == 1
        assert first.rows_skipped_missing_match == 0
        assert first.ht_updates == 1
        assert first.statistics_upserted == 2
        assert first.goal_events_reused == 1
        assert first.goal_events_added == 2

        match = db.execute(
            text(
                """
                SELECT home_score_ht, away_score_ht
                FROM matches WHERE id = :match_id
                """
            ),
            {"match_id": match_id},
        ).one()
        assert match.home_score_ht == 1
        assert match.away_score_ht == 0

        stats = db.execute(
            text(
                """
                SELECT team_id, shots, corners, possession
                FROM match_team_statistics
                WHERE match_id = :match_id AND period = 'FULL_TIME'
                ORDER BY team_id
                """
            ),
            {"match_id": match_id},
        ).all()
        assert len(stats) == 2
        assert {row.team_id for row in stats} == {home_id, away_id}

        goal_count = db.execute(
            text(
                """
                SELECT count(*) FROM match_events
                WHERE match_id = :match_id AND event_type = 'goal'
                """
            ),
            {"match_id": match_id},
        ).scalar_one()
        assert goal_count == 3

        second = importer.import_file(csv_path)
        assert second.goal_events_added == 0
        assert second.goal_events_reused == 3

        goal_count_after = db.execute(
            text(
                """
                SELECT count(*) FROM match_events
                WHERE match_id = :match_id AND event_type = 'goal'
                """
            ),
            {"match_id": match_id},
        ).scalar_one()
        assert goal_count_after == 3

        _cleanup(db)
