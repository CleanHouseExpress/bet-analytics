import os
from datetime import datetime
from typing import Any

import httpx

from apps.api.app.providers.base import FootballDataProvider
from apps.api.app.providers.contracts import (
    ProviderCompetition,
    ProviderFixture,
    ProviderFixtureStatistics,
    ProviderSeason,
    ProviderTeam,
)

SPORTMONKS_BASE_URL = "https://api.sportmonks.com/v3/football"


class SportmonksProvider(FootballDataProvider):
    name = "sportmonks"

    def __init__(
        self,
        api_token: str | None = None,
        base_url: str = SPORTMONKS_BASE_URL,
        timeout: float = 30.0,
    ) -> None:
        self.api_token = api_token or os.getenv("SPORTMONKS_API_TOKEN", "")
        if not self.api_token:
            raise ValueError("SPORTMONKS_API_TOKEN is required")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def _get_all(
        self,
        path: str,
        params: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        page = 1
        items: list[dict[str, Any]] = []
        query = dict(params or {})
        query.setdefault("per_page", "50")

        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            headers={
                "Authorization": self.api_token,
                "Accept": "application/json",
            },
        ) as client:
            while True:
                query["page"] = str(page)
                response = await client.get(path, params=query)
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data", [])
                if isinstance(data, dict):
                    data = [data]
                items.extend(data)

                pagination = payload.get("pagination") or {}
                if not pagination.get("has_more"):
                    break
                page += 1

        return items

    async def get_competitions(self) -> list[ProviderCompetition]:
        leagues = await self._get_all("/leagues", {"include": "country"})
        return [
            ProviderCompetition(
                external_id=str(item["id"]),
                name=item["name"],
                country_code=(item.get("country") or {}).get("iso2"),
                raw=item,
            )
            for item in leagues
        ]

    async def get_seasons(self, competition_external_id: str) -> list[ProviderSeason]:
        seasons = await self._get_all(
            "/seasons",
            {"filters": f"seasonLeagues:{competition_external_id}"},
        )
        return [
            ProviderSeason(
                external_id=str(item["id"]),
                competition_external_id=str(item["league_id"]),
                name=item["name"],
                start_date=self._parse_datetime(item.get("starting_at")),
                end_date=self._parse_datetime(item.get("ending_at")),
                raw=item,
            )
            for item in seasons
        ]

    async def get_teams(self, season_external_id: str) -> list[ProviderTeam]:
        teams = await self._get_all(f"/teams/seasons/{season_external_id}")
        return [
            ProviderTeam(
                external_id=str(item["id"]),
                name=item["name"],
                raw=item,
            )
            for item in teams
        ]

    async def get_fixtures(self, season_external_id: str) -> list[ProviderFixture]:
        fixtures = await self._get_all(
            f"/fixtures/seasons/{season_external_id}",
            {"include": "participants;scores;state"},
        )
        return [self._map_fixture(item) for item in fixtures]

    async def get_fixture_statistics(
        self, fixture_external_id: str
    ) -> ProviderFixtureStatistics:
        fixtures = await self._get_all(
            f"/fixtures/{fixture_external_id}",
            {"include": "statistics"},
        )
        if not fixtures:
            raise ValueError(f"Fixture '{fixture_external_id}' was not found")

        fixture = fixtures[0]
        metrics: dict[str, Any] = {}
        for statistic in fixture.get("statistics") or []:
            key = str(statistic.get("type_id", statistic.get("id", "unknown")))
            metrics[key] = statistic.get("data", statistic.get("value", statistic))

        return ProviderFixtureStatistics(
            fixture_external_id=fixture_external_id,
            metrics=metrics,
            raw=fixture,
        )

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    def _map_fixture(self, item: dict[str, Any]) -> ProviderFixture:
        home_id: str | None = None
        away_id: str | None = None
        for participant in item.get("participants") or []:
            location = (participant.get("meta") or {}).get("location")
            if location == "home":
                home_id = str(participant["id"])
            elif location == "away":
                away_id = str(participant["id"])

        if home_id is None or away_id is None:
            raise ValueError(f"Fixture {item.get('id')} is missing home/away participants")

        home_score = self._current_score(item.get("scores") or [], "home")
        away_score = self._current_score(item.get("scores") or [], "away")
        state = item.get("state") or {}

        return ProviderFixture(
            external_id=str(item["id"]),
            season_external_id=str(item["season_id"]),
            home_team_external_id=home_id,
            away_team_external_id=away_id,
            kickoff_at=self._parse_datetime(item["starting_at"]),
            status=str(state.get("state") or item.get("state_id") or "unknown"),
            home_score=home_score,
            away_score=away_score,
            raw=item,
        )

    @staticmethod
    def _current_score(scores: list[dict[str, Any]], participant: str) -> int | None:
        for score in reversed(scores):
            score_data = score.get("score") or {}
            if score_data.get("participant") == participant and "goals" in score_data:
                return int(score_data["goals"])
        return None
