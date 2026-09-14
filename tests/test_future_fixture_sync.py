import asyncio
from datetime import UTC, date, datetime

import pytest
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


class FakeFutureProvider(FootballDataProvider):
    name = "fake-future-fixtures"

    def __init__(self) -> None:
        self.kickoff = datetime(2097, 9, 20, 19, 0, tzinfo=UTC)
        self.status = "scheduled"
        self.fail_with_unknown_team = False
        self.requested_window: tuple[date, date] | None = None

    async def get_competitions(self) -> list[ProviderCompetition]:
        return [
            ProviderCompetition(
                external_id="future-comp",
                name="Future Fixture Test League",
                country_code="BRA",
                raw={"id": "future-comp"},
            )
        ]

    async def get_seasons(self, competition_external_id: str) -> list[ProviderSeason]:
        return [
            ProviderSeason(
                external_id="future-season",
                competition_external_id=competition_external_id,
                name="2097",
                start_date=datetime(2097, 1, 1, tzinfo=UTC),
                end_date=datetime(2097, 12, 31, tzinfo=UTC),
                raw={"id": "future-season"},
            )
        ]

    async def get_teams(self, season_external_id: str) -> list[ProviderTeam]:
        teams = [
            ProviderTeam(
                external_id="future-home",
                name="Future Home FC",
                country_code="BRA",
                raw={"id": "future-home"},
            ),
            ProviderTeam(
                external_id="future-away",
                name="Future Away FC",
                country_code="BRA",
                raw={"id": "future-away"},
            ),
        ]
        if self.fail_with_unknown_team:
            teams.append(
                ProviderTeam(
                    external_id="unknown-team",
                    name="Provider Unknown FC",
                    country_code="BRA",
                )
            )
        return teams

    async def get_fixtures(self, season_external_id: str) -> list[ProviderFixture]:
        raise AssertionError("window sync must use get_fixtures_between")

    async def get_fixtures_between(
        self,
        season_external_id: str,
        date_from: date,
        date_to: date,
    ) -> list[ProviderFixture]:
        self.requested_window = (date_from, date_to)
        return [
            ProviderFixture(
                external_id="future-match",
                season_external_id=season_external_id,
                home_team_external_id="future-home",
                away_team_external_id="future-away",
                kickoff_at=self.kickoff,
                status=self.status,
                round_number=28,
                provider_updated_at=datetime(2097, 9, 14, 12, 0, tzinfo=UTC),
                raw={"id": "future-match", "status": self.status},
            )
        ]

    async def get_fixture_statistics(
        self, fixture_external_id: str
    ) -> ProviderFixtureStatistics:
        return ProviderFixtureStatistics(fixture_external_id=fixture_external_id, metrics={})


def _cleanup(db) -> None:
    match_ids = db.execute(
        text(
            """
            SELECT internal_id FROM external_entity_mappings
            WHERE provider = :provider AND entity_type = 'match'
            """
        ),
        {"provider": FakeFutureProvider.name},
    ).scalars().all()
    if match_ids:
        db.execute(text("DELETE FROM matches WHERE id = ANY(:ids)"), {"ids": match_ids})

    db.execute(
        text("DELETE FROM external_entity_mappings WHERE provider = :provider"),
        {"provider": FakeFutureProvider.name},
    )
    db.execute(
        text("DELETE FROM provider_raw_payloads WHERE provider = :provider"),
        {"provider": FakeFutureProvider.name},
    )
    db.execute(
        text("DELETE FROM provider_sync_runs WHERE provider = :provider"),
        {"provider": FakeFutureProvider.name},
    )
    db.execute(
        text("DELETE FROM competitions WHERE name = 'Future Fixture Test League'")
    )
    db.execute(
        text("DELETE FROM teams WHERE name IN ('Future Home FC', 'Future Away FC')")
    )
    db.commit()


def _seed_canonical_teams(db) -> None:
    for name in ("Future Home FC", "Future Away FC"):
        exists = db.execute(
            text("SELECT id FROM teams WHERE name = :name ORDER BY id LIMIT 1"),
            {"name": name},
        ).scalar_one_or_none()
        if exists is None:
            db.execute(
                text(
                    """
                    INSERT INTO teams (name, country_code, created_at, updated_at)
                    VALUES (:name, 'BRA', now(), now())
                    """
                ),
                {"name": name},
            )
    db.commit()


def test_future_fixture_sync_is_idempotent_and_updates_schedule() -> None:
    provider = FakeFutureProvider()
    date_from = date(2097, 9, 18)
    date_to = date(2097, 11, 4)

    with SessionLocal() as db:
        _cleanup(db)
        _seed_canonical_teams(db)
        service = FootballIngestionService(db, provider)

        first = asyncio.run(
            service.sync_season(
                "future-comp",
                "future-season",
                fixture_date_from=date_from,
                fixture_date_to=date_to,
                strict_team_reconciliation=True,
            )
        )
        assert provider.requested_window == (date_from, date_to)
        assert first.matches_created == 1
        assert first.teams_created == 0

        provider.kickoff = datetime(2097, 9, 21, 21, 30, tzinfo=UTC)
        provider.status = "postponed"
        second = asyncio.run(
            service.sync_season(
                "future-comp",
                "future-season",
                fixture_date_from=date_from,
                fixture_date_to=date_to,
                strict_team_reconciliation=True,
            )
        )
        assert second.matches_created == 0
        assert second.matches_updated == 1

        match = db.execute(
            text(
                """
                SELECT m.kickoff_at, m.status, m.round_id,
                       m.provider_updated_at, m.last_synced_at
                FROM matches m
                JOIN external_entity_mappings e
                  ON e.internal_id = m.id
                 AND e.entity_type = 'match'
                WHERE e.provider = :provider
                  AND e.external_id = 'future-match'
                """
            ),
            {"provider": provider.name},
        ).one()
        assert match.kickoff_at == provider.kickoff
        assert match.status == "postponed"
        assert match.round_id is not None
        assert match.provider_updated_at is not None
        assert match.last_synced_at is not None

        successful_runs = db.execute(
            text(
                """
                SELECT count(*) FROM provider_sync_runs
                WHERE provider = :provider
                  AND resource = 'fixture_window'
                  AND status = 'success'
                """
            ),
            {"provider": provider.name},
        ).scalar_one()
        assert successful_runs == 2

        _cleanup(db)


def test_strict_reconciliation_fails_without_partial_fixture_changes() -> None:
    provider = FakeFutureProvider()
    provider.fail_with_unknown_team = True

    with SessionLocal() as db:
        _cleanup(db)
        _seed_canonical_teams(db)
        service = FootballIngestionService(db, provider)

        with pytest.raises(ValueError, match="Team reconciliation failed"):
            asyncio.run(
                service.sync_season(
                    "future-comp",
                    "future-season",
                    fixture_date_from=date(2097, 9, 18),
                    fixture_date_to=date(2097, 11, 4),
                    strict_team_reconciliation=True,
                )
            )

        match_count = db.execute(
            text(
                """
                SELECT count(*) FROM external_entity_mappings
                WHERE provider = :provider AND entity_type = 'match'
                """
            ),
            {"provider": provider.name},
        ).scalar_one()
        assert match_count == 0

        failed_runs = db.execute(
            text(
                """
                SELECT count(*) FROM provider_sync_runs
                WHERE provider = :provider AND status = 'failed'
                """
            ),
            {"provider": provider.name},
        ).scalar_one()
        assert failed_runs == 1

        _cleanup(db)
