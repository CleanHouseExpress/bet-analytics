from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class ProviderCompetition:
    external_id: str
    name: str
    country_code: str | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ProviderSeason:
    external_id: str
    competition_external_id: str
    name: str
    start_date: datetime | None = None
    end_date: datetime | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ProviderTeam:
    external_id: str
    name: str
    country_code: str | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ProviderFixture:
    external_id: str
    season_external_id: str
    home_team_external_id: str
    away_team_external_id: str
    kickoff_at: datetime
    status: str
    home_score: int | None = None
    away_score: int | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ProviderFixtureStatistics:
    fixture_external_id: str
    metrics: dict[str, Any]
    raw: dict[str, Any] | None = None
