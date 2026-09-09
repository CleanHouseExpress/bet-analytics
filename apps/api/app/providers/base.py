from abc import ABC, abstractmethod

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

    @abstractmethod
    async def get_fixture_statistics(
        self, fixture_external_id: str
    ) -> ProviderFixtureStatistics:
        raise NotImplementedError
