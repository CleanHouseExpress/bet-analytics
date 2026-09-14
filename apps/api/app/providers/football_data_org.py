import os
from datetime import date, datetime
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

FOOTBALL_DATA_BASE_URL = "https://api.football-data.org/v4"


class FootballDataOrgProvider(FootballDataProvider):
    """Adapter for football-data.org v4.

    The provider contract addresses teams/fixtures by ``season_external_id``.
    football-data.org exposes those resources through a competition + season-year
    pair, so season context is cached when ``get_seasons`` is called. The ingestion
    service follows that lifecycle (competition -> seasons -> teams -> fixtures).
    """

    name = "football-data"

    def __init__(
        self,
        api_token: str | None = None,
        base_url: str = FOOTBALL_DATA_BASE_URL,
        timeout: float = 30.0,
    ) -> None:
        self.api_token = api_token or os.getenv("FOOTBALL_DATA_API_TOKEN", "")
        if not self.api_token:
            raise ValueError("FOOTBALL_DATA_API_TOKEN is required")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._season_context: dict[str, tuple[str, int]] = {}

    async def _get(
        self,
        path: str,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            headers={
                "X-Auth-Token": self.api_token,
                "Accept": "application/json",
            },
        ) as client:
            response = await client.get(path, params=params)
            response.raise_for_status()
            payload = response.json()

        if not isinstance(payload, dict):
            raise ValueError("football-data.org returned an unexpected payload")
        return payload

    async def get_competitions(self) -> list[ProviderCompetition]:
        payload = await self._get("/competitions")
        competitions = payload.get("competitions") or []
        return [
            ProviderCompetition(
                external_id=str(item["id"]),
                name=str(item["name"]),
                country_code=(item.get("area") or {}).get("code"),
                raw=item,
            )
            for item in competitions
        ]

    async def get_seasons(self, competition_external_id: str) -> list[ProviderSeason]:
        payload = await self._get(f"/competitions/{competition_external_id}")
        seasons = payload.get("seasons") or []
        result: list[ProviderSeason] = []

        for item in seasons:
            external_id = str(item["id"])
            start_date = self._parse_datetime(item.get("startDate"))
            end_date = self._parse_datetime(item.get("endDate"))
            if start_date is None:
                continue

            self._season_context[external_id] = (
                competition_external_id,
                start_date.year,
            )
            result.append(
                ProviderSeason(
                    external_id=external_id,
                    competition_external_id=competition_external_id,
                    name=str(start_date.year),
                    start_date=start_date,
                    end_date=end_date,
                    raw=item,
                )
            )

        return result

    async def get_teams(self, season_external_id: str) -> list[ProviderTeam]:
        competition_external_id, season_year = self._require_season_context(
            season_external_id
        )
        payload = await self._get(
            f"/competitions/{competition_external_id}/teams",
            {"season": str(season_year)},
        )
        teams = payload.get("teams") or []
        return [
            ProviderTeam(
                external_id=str(item["id"]),
                name=str(item.get("shortName") or item["name"]),
                country_code=(item.get("area") or {}).get("code"),
                raw=item,
            )
            for item in teams
        ]

    async def get_fixtures(self, season_external_id: str) -> list[ProviderFixture]:
        competition_external_id, season_year = self._require_season_context(
            season_external_id
        )
        payload = await self._get(
            f"/competitions/{competition_external_id}/matches",
            {"season": str(season_year)},
        )
        matches = payload.get("matches") or []
        return [self._map_fixture(item) for item in matches]

    async def get_fixtures_between(
        self,
        season_external_id: str,
        date_from: date,
        date_to: date,
    ) -> list[ProviderFixture]:
        competition_external_id, season_year = self._require_season_context(
            season_external_id
        )
        payload = await self._get(
            f"/competitions/{competition_external_id}/matches",
            {
                "season": str(season_year),
                "dateFrom": date_from.isoformat(),
                "dateTo": date_to.isoformat(),
            },
        )
        matches = payload.get("matches") or []
        return [self._map_fixture(item) for item in matches]

    async def get_fixture_statistics(
        self, fixture_external_id: str
    ) -> ProviderFixtureStatistics:
        payload = await self._get(f"/matches/{fixture_external_id}")
        return ProviderFixtureStatistics(
            fixture_external_id=fixture_external_id,
            metrics={},
            raw=payload,
        )

    def _require_season_context(self, season_external_id: str) -> tuple[str, int]:
        try:
            return self._season_context[season_external_id]
        except KeyError as exc:
            raise ValueError(
                "Season context is unknown. Call get_seasons() before requesting "
                f"teams or fixtures for season '{season_external_id}'."
            ) from exc

    def _map_fixture(self, item: dict[str, Any]) -> ProviderFixture:
        season = item.get("season") or {}
        home_team = item.get("homeTeam") or {}
        away_team = item.get("awayTeam") or {}
        score = item.get("score") or {}
        full_time = score.get("fullTime") or {}
        kickoff_at = self._parse_datetime(item.get("utcDate"))

        if kickoff_at is None:
            raise ValueError(f"Match {item.get('id')} is missing utcDate")
        if season.get("id") is None:
            raise ValueError(f"Match {item.get('id')} is missing season")
        if home_team.get("id") is None or away_team.get("id") is None:
            raise ValueError(f"Match {item.get('id')} is missing home/away teams")

        return ProviderFixture(
            external_id=str(item["id"]),
            season_external_id=str(season["id"]),
            home_team_external_id=str(home_team["id"]),
            away_team_external_id=str(away_team["id"]),
            kickoff_at=kickoff_at,
            status=str(item.get("status") or "UNKNOWN").lower(),
            home_score=self._to_int(full_time.get("home")),
            away_score=self._to_int(full_time.get("away")),
            round_number=self._to_int(item.get("matchday")),
            stage=str(item.get("stage")) if item.get("stage") else None,
            provider_updated_at=self._parse_datetime(item.get("lastUpdated")),
            raw=item,
        )

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        normalized = value.strip()
        if len(normalized) == 10:
            normalized = f"{normalized}T00:00:00+00:00"
        else:
            normalized = normalized.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)

    @staticmethod
    def _to_int(value: Any) -> int | None:
        if value is None:
            return None
        return int(value)
