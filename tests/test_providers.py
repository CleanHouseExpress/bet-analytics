from datetime import datetime

import pytest

from apps.api.app.providers.base import FootballDataProvider
from apps.api.app.providers.factory import build_provider_registry
from apps.api.app.providers.sportmonks import SportmonksProvider


def test_provider_registry_exposes_sportmonks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPORTMONKS_API_TOKEN", "test-token")
    registry = build_provider_registry()

    assert registry.names == ("sportmonks",)
    provider = registry.create("sportmonks")
    assert isinstance(provider, FootballDataProvider)
    assert isinstance(provider, SportmonksProvider)


def test_provider_registry_rejects_unknown_provider() -> None:
    registry = build_provider_registry()

    with pytest.raises(ValueError, match="Unknown provider"):
        registry.create("unknown")


def test_sportmonks_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPORTMONKS_API_TOKEN", raising=False)

    with pytest.raises(ValueError, match="SPORTMONKS_API_TOKEN is required"):
        SportmonksProvider()


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
