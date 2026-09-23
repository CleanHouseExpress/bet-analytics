import asyncio
from datetime import UTC, datetime

import pytest

from apps.api.app.providers.base import FootballDataProvider
from apps.api.app.providers.factory import build_provider_registry
from apps.api.app.providers.football_data_org import FootballDataOrgProvider
from apps.api.app.providers.sportmonks import SportmonksProvider


def test_provider_registry_exposes_supported_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPORTMONKS_API_TOKEN", "test-token")
    monkeypatch.setenv("FOOTBALL_DATA_API_TOKEN", "test-token")
    registry = build_provider_registry()

    assert registry.names == ("football-data", "sportmonks")

    football_data = registry.create("football-data")
    assert isinstance(football_data, FootballDataProvider)
    assert isinstance(football_data, FootballDataOrgProvider)

    sportmonks = registry.create("sportmonks")
    assert isinstance(sportmonks, FootballDataProvider)
    assert isinstance(sportmonks, SportmonksProvider)


def test_provider_registry_rejects_unknown_provider() -> None:
    registry = build_provider_registry()

    with pytest.raises(ValueError, match="Unknown provider"):
        registry.create("unknown")


def test_sportmonks_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPORTMONKS_API_TOKEN", raising=False)

    with pytest.raises(ValueError, match="SPORTMONKS_API_TOKEN is required"):
        SportmonksProvider()


def test_football_data_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FOOTBALL_DATA_API_TOKEN", raising=False)

    with pytest.raises(ValueError, match="FOOTBALL_DATA_API_TOKEN is required"):
        FootballDataOrgProvider()


def test_sportmonks_maps_fixture() -> None:
    provider = SportmonksProvider(api_token="test-token")
    fixture = provider._map_fixture(
        {
            "id": 123,
            "season_id": 456,
            "starting_at": "2026-09-08 19:30:00",
            "state_id": 5,
            "state": {"state": "FT"},
            "participants": [
                {"id": 10, "meta": {"location": "home"}},
                {"id": 20, "meta": {"location": "away"}},
            ],
            "scores": [
                {"score": {"participant": "home", "goals": 2}},
                {"score": {"participant": "away", "goals": 1}},
            ],
        }
    )

    assert fixture.external_id == "123"
    assert fixture.season_external_id == "456"
    assert fixture.home_team_external_id == "10"
    assert fixture.away_team_external_id == "20"
    assert fixture.kickoff_at == datetime(2026, 9, 8, 19, 30)
    assert fixture.status == "FT"
    assert fixture.home_score == 2
    assert fixture.away_score == 1


def test_football_data_maps_fixture() -> None:
    provider = FootballDataOrgProvider(api_token="test-token")
    fixture = provider._map_fixture(
        {
            "id": 987654,
            "utcDate": "2026-09-20T19:00:00Z",
            "status": "SCHEDULED",
            "season": {"id": 2026},
            "homeTeam": {"id": 10, "name": "Home FC"},
            "awayTeam": {"id": 20, "name": "Away FC"},
            "score": {
                "winner": None,
                "fullTime": {"home": None, "away": None},
            },
        }
    )

    assert fixture.external_id == "987654"
    assert fixture.season_external_id == "2026"
    assert fixture.home_team_external_id == "10"
    assert fixture.away_team_external_id == "20"
    assert fixture.kickoff_at == datetime(2026, 9, 20, 19, 0, tzinfo=UTC)
    assert fixture.status == "scheduled"
    assert fixture.home_score is None
    assert fixture.away_score is None


def test_football_data_propagates_explicit_competition_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FootballDataOrgProvider(api_token="test-token")

    async def fake_get(path: str, params=None):
        assert path == "/competitions"
        assert params is None
        return {
            "competitions": [
                {
                    "id": 2013,
                    "name": "Brasileirão Série A",
                    "type": "LEAGUE",
                    "area": {"code": "BRA"},
                }
            ]
        }

    monkeypatch.setattr(provider, "_get", fake_get)
    competitions = asyncio.run(provider.get_competitions())

    assert len(competitions) == 1
    assert competitions[0].competition_type == "LEAGUE"


def test_football_data_requires_season_discovery_before_teams_or_fixtures() -> None:
    provider = FootballDataOrgProvider(api_token="test-token")

    with pytest.raises(ValueError, match="Season context is unknown"):
        provider._require_season_context("missing-season")
