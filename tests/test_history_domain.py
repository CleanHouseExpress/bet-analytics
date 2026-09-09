from datetime import UTC, date, datetime

from sqlalchemy import inspect, select

from apps.api.app.core.database import SessionLocal, engine
from apps.api.app.domain.history import Competition, Match, Season, Team


def test_history_tables_exist() -> None:
    tables = set(inspect(engine).get_table_names())

    assert {"competitions", "seasons", "teams", "matches"}.issubset(tables)


def test_can_persist_historical_match() -> None:
    now = datetime.now(UTC)

    with SessionLocal() as session:
        competition = Competition(
            name="Brasileirao Serie A",
            country_code="BRA",
            competition_type="league",
            created_at=now,
            updated_at=now,
        )
        home_team = Team(
            name="Palmeiras",
            short_name="Palmeiras",
            country_code="BRA",
            created_at=now,
            updated_at=now,
        )
        away_team = Team(
            name="Flamengo",
            short_name="Flamengo",
            country_code="BRA",
            created_at=now,
            updated_at=now,
        )
        session.add_all([competition, home_team, away_team])
        session.flush()

        season = Season(
            competition_id=competition.id,
            name="2026",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            is_current=True,
            created_at=now,
            updated_at=now,
        )
        session.add(season)
        session.flush()

        match = Match(
            competition_id=competition.id,
            season_id=season.id,
            home_team_id=home_team.id,
            away_team_id=away_team.id,
            kickoff_at=datetime(2026, 9, 20, 19, 30, tzinfo=UTC),
            status="finished",
            home_score=2,
            away_score=1,
            created_at=now,
            updated_at=now,
        )
        session.add(match)
        session.commit()

        persisted = session.scalar(select(Match).where(Match.id == match.id))

        assert persisted is not None
        assert persisted.home_team.name == "Palmeiras"
        assert persisted.away_team.name == "Flamengo"
        assert persisted.season.name == "2026"
        assert persisted.home_score == 2
        assert persisted.away_score == 1

        session.delete(match)
        session.delete(season)
        session.delete(home_team)
        session.delete(away_team)
        session.delete(competition)
        session.commit()
