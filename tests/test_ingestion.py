import asyncio
from datetime import UTC, datetime

from sqlalchemy import text

from apps.api.app.core.database import SessionLocal
from apps.api.app.providers.base import FootballDataProvider
from apps.api.app.providers.contracts import (
    ProviderCompetition,
    ProviderFixture,
    ProviderFixtureStatistics,
    ProviderSeason,
    ProviderTeam,
)
from apps.api.app.services.ingestion import FootballIngestionService


class FakeProvider(FootballDataProvider):
    name = "fake-ingestion-test"

    def __init__(self) -> None:
        self.home_score = 2
        self.away_score = 1

    async def get_competitions(self) -> list[ProviderCompetition]:
        return [
            ProviderCompetition(
                external_id="comp-1",
                name="Test League",
                country_code="BRA",
                raw={"id": "comp-1", "name": "Test League"},
            )
        ]

    async def get_seasons(self, competition_external_id: str) -> list[ProviderSeason]:
        assert competition_external_id == "comp-1"
        return [
            ProviderSeason(
                external_id="season-1",
                competition_external_id="comp-1",
                name="2099",
                start_date=datetime(2099, 1, 1, tzinfo=UTC),
                end_date=datetime(2099, 12, 31, tzinfo=UTC),
                raw={"id": "season-1"},
            )
        ]

    async def get_teams(self, season_external_id: str) -> list[ProviderTeam]:
        assert season_external_id == "season-1"
        return [
            ProviderTeam(
                external_id="team-home",
                name="Test Palmeiras",
                country_code="BRA",
                raw={"id": "team-home"},
            ),
            ProviderTeam(
                external_id="team-away",
                name="Test Flamengo",
                country_code="BRA",
                raw={"id": "team-away"},
            ),
        ]

    async def get_fixtures(self, season_external_id: str) -> list[ProviderFixture]:
        assert season_external_id == "season-1"
        return [
            ProviderFixture(
                external_id="fixture-1",
                season_external_id="season-1",
                home_team_external_id="team-home",
                away_team_external_id="team-away",
                kickoff_at=datetime(2099, 9, 8, 22, 0, tzinfo=UTC),
                status="finished",
                home_score=self.home_score,
                away_score=self.away_score,
                raw={"id": "fixture-1", "score": [self.home_score, self.away_score]},
            )
        ]

    async def get_fixture_statistics(
        self, fixture_external_id: str
    ) -> ProviderFixtureStatistics:
        return ProviderFixtureStatistics(fixture_external_id=fixture_external_id, metrics={})


def _count(db, table: str, provider: str, entity_type: str) -> int:
    return db.execute(
        text(
            f"""
            SELECT count(*)
            FROM {table} entity
            JOIN external_entity_mappings mapping
              ON mapping.internal_id = entity.id
            WHERE mapping.provider = :provider
              AND mapping.entity_type = :entity_type
            """
        ),
        {"provider": provider, "entity_type": entity_type},
    ).scalar_one()


def _cleanup(db) -> None:
    match_ids = db.execute(
        text(
            """
            SELECT internal_id FROM external_entity_mappings
            WHERE provider = :provider AND entity_type = 'match'
            """
        ),
        {"provider": FakeProvider.name},
    ).scalars().all()
    season_ids = db.execute(
        text(
            """
            SELECT internal_id FROM external_entity_mappings
            WHERE provider = :provider AND entity_type = 'season'
            """
        ),
        {"provider": FakeProvider.name},
    ).scalars().all()
    team_ids = db.execute(
        text(
            """
            SELECT internal_id FROM external_entity_mappings
            WHERE provider = :provider AND entity_type = 'team'
            """
        ),
        {"provider": FakeProvider.name},
    ).scalars().all()
    competition_ids = db.execute(
        text(
            """
            SELECT internal_id FROM external_entity_mappings
            WHERE provider = :provider AND entity_type = 'competition'
            """
        ),
        {"provider": FakeProvider.name},
    ).scalars().all()

    db.execute(
        text("DELETE FROM external_entity_mappings WHERE provider = :provider"),
        {"provider": FakeProvider.name},
    )
    db.execute(
        text("DELETE FROM provider_raw_payloads WHERE provider = :provider"),
        {"provider": FakeProvider.name},
    )
    db.execute(
        text("DELETE FROM provider_sync_runs WHERE provider = :provider"),
        {"provider": FakeProvider.name},
    )

    if match_ids:
        db.execute(text("DELETE FROM matches WHERE id = ANY(:ids)"), {"ids": match_ids})
    if season_ids:
        db.execute(text("DELETE FROM seasons WHERE id = ANY(:ids)"), {"ids": season_ids})
    if team_ids:
        db.execute(text("DELETE FROM teams WHERE id = ANY(:ids)"), {"ids": team_ids})
    if competition_ids:
        db.execute(
            text("DELETE FROM competitions WHERE id = ANY(:ids)"),
            {"ids": competition_ids},
        )
    db.commit()


def test_season_sync_is_idempotent_and_updates_existing_match() -> None:
    provider = FakeProvider()

    with SessionLocal() as db:
        _cleanup(db)
        service = FootballIngestionService(db, provider)

        first = asyncio.run(service.sync_season("comp-1", "season-1"))

        assert first.competitions_created == 1
        assert first.seasons_created == 1
        assert first.teams_created == 2
        assert first.matches_created == 1
        assert _count(db, "competitions", provider.name, "competition") == 1
        assert _count(db, "seasons", provider.name, "season") == 1
        assert _count(db, "teams", provider.name, "team") == 2
        assert _count(db, "matches", provider.name, "match") == 1

        provider.home_score = 3
        second = asyncio.run(service.sync_season("comp-1", "season-1"))

        assert second.records_created == 0
        assert second.competitions_updated == 1
        assert second.seasons_updated == 1
        assert second.teams_updated == 2
        assert second.matches_updated == 1

        score = db.execute(
            text(
                """
                SELECT m.home_score, m.away_score
                FROM matches m
                JOIN external_entity_mappings mapping
                  ON mapping.internal_id = m.id
                WHERE mapping.provider = :provider
                  AND mapping.entity_type = 'match'
                  AND mapping.external_id = 'fixture-1'
                """
            ),
            {"provider": provider.name},
        ).one()
        assert score.home_score == 3
        assert score.away_score == 1

        raw_count = db.execute(
            text(
                """
                SELECT count(*) FROM provider_raw_payloads
                WHERE provider = :provider
                """
            ),
            {"provider": provider.name},
        ).scalar_one()
        assert raw_count == 10

        successful_runs = db.execute(
            text(
                """
                SELECT count(*) FROM provider_sync_runs
                WHERE provider = :provider AND status = 'success'
                """
            ),
            {"provider": provider.name},
        ).scalar_one()
        assert successful_runs == 2

        _cleanup(db)
