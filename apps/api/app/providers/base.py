from abc import ABC, abstractmethod
from datetime import date

from apps.api.app.providers.contracts import (
    ProviderCompetition,
    ProviderFixture,
    ProviderFixtureStatistics,
    ProviderSeason,
    ProviderTeam,
)


class FootballDataProvider(ABC):
    name: str

    @abstractmethod
    async def get_competitions(self) -> list[ProviderCompetition]:
        raise NotImplementedError

    @abstractmethod
    async def get_seasons(self, competition_external_id: str) -> list[ProviderSeason]:
        raise NotImplementedError

    @abstractmethod
    async def get_teams(self, season_external_id: str) -> list[ProviderTeam]:
        raise NotImplementedError

    @abstractmethod
    async def get_fixtures(self, season_external_id: str) -> list[ProviderFixture]:
        raise NotImplementedError

    async def get_fixtures_between(
        self,
        season_external_id: str,
        date_from: date,
        date_to: date,
    ) -> list[ProviderFixture]:
        """Return fixtures within an inclusive date window.

        Providers with a native date filter should override this method. The fallback
        preserves compatibility by fetching the season and filtering locally.
        """
        fixtures = await self.get_fixtures(season_external_id)
        return [
            fixture
            for fixture in fixtures
            if date_from <= fixture.kickoff_at.date() <= date_to
        ]

    @abstractmethod
    async def get_fixture_statistics(
        self, fixture_external_id: str
    ) -> ProviderFixtureStatistics:
        raise NotImplementedError
