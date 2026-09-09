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
from apps.api.app.services.brazilian_leagues_sync import BrazilianLeaguesCatalogSync


class FakeBrazilProvider(FootballDataProvider):
    name = "fake-brazil-catalog"

    async def get_competitions(self) -> list[ProviderCompetition]:
        return [
            ProviderCompetition(
                external_id="league-a",
                name="Serie A",
                country_code="BR",
            )
        ]

    async def get_seasons(self, competition_external_id: str) -> list[ProviderSeason]:
        return [
            ProviderSeason(
                external_id="season-a-2098",
                competition_external_id=competition_external_id,
                name="2098",
                start_date=datetime(2098, 1, 1, tzinfo=UTC),
                end_date=datetime(2098, 12, 31, tzinfo=UTC),
            )
        ]

    async def get_teams(self, season_external_id: str) -> list[ProviderTeam]:
        return [
            ProviderTeam(external_id="team-existing", name="Catalog Palmeiras", country_code="BR"),
            ProviderTeam(external_id="team-new", name="Catalog Novo Clube", country_code="BR"),
        ]

    async def get_fixtures(self, season_external_id: str) -> list[ProviderFixture]:
        raise AssertionError("catalog sync must not fetch fixtures")

    async def get_fixture_statistics(self, fixture_external_id: str) -> ProviderFixtureStatistics:
        raise AssertionError("catalog sync must not fetch statistics")


def test_catalog_sync_reuses_existing_team_and_is_idempotent() -> None:
    with SessionLocal() as db:
        db.execute(text("DELETE FROM external_entity_mappings WHERE provider = :p"), {"p": FakeBrazilProvider.name})
        db.execute(text("DELETE FROM team_aliases WHERE alias LIKE 'Catalog %'"))
        db.execute(text("DELETE FROM teams WHERE name LIKE 'Catalog %'"))
        db.execute(text("DELETE FROM seasons WHERE name = '2098'"))
        db.execute(text("DELETE FROM competitions WHERE name = 'Brasileirão Série A' AND id NOT IN (SELECT competition_id FROM seasons)"))
        existing_id = db.execute(
            text(
                """
                INSERT INTO teams (name, country_code, created_at, updated_at)
                VALUES ('Catalog Palmeiras', 'BRA', now(), now())
                RETURNING id
                """
            )
        ).scalar_one()
        db.commit()

        service = BrazilianLeaguesCatalogSync(db, FakeBrazilProvider())
        first = asyncio.run(service.sync(2098, ["A"]))[0]
        second = asyncio.run(service.sync(2098, ["A"]))[0]

        assert first.teams_seen == 2
        assert first.teams_created == 1
        assert first.teams_reused == 1
        assert first.teams_mapped == 2
        assert first.ambiguous == []
        assert second.teams_created == 0
        assert second.teams_reused == 2

        mapped_id = db.execute(
            text(
                """
                SELECT internal_id FROM external_entity_mappings
                WHERE provider = :provider
                  AND entity_type = 'team'
                  AND external_id = 'team-existing'
                """
            ),
            {"provider": FakeBrazilProvider.name},
        ).scalar_one()
        assert mapped_id == existing_id

        db.execute(text("DELETE FROM external_entity_mappings WHERE provider = :p"), {"p": FakeBrazilProvider.name})
        db.execute(text("DELETE FROM team_aliases WHERE alias LIKE 'Catalog %'"))
        db.execute(text("DELETE FROM teams WHERE name LIKE 'Catalog %'"))
        db.execute(text("DELETE FROM seasons WHERE name = '2098'"))
        db.execute(text("DELETE FROM competitions WHERE name = 'Brasileirão Série A' AND id NOT IN (SELECT competition_id FROM seasons)"))
        db.commit()
