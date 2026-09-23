from __future__ import annotations

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
from apps.api.app.providers.team_aliases import (
    PROVIDER_TEAM_ALIAS_CATALOG_VERSION,
    ProviderTeamAliasCatalogError,
    ProviderTeamAliasRule,
    canonical_names_for_provider_team,
    validate_provider_team_alias_catalog,
)
from apps.api.app.services.ingestion import FootballIngestionService


class FootballDataAliasTestProvider(FootballDataProvider):
    name = "football-data"

    async def get_competitions(self) -> list[ProviderCompetition]:
        return []

    async def get_seasons(self, competition_external_id: str) -> list[ProviderSeason]:
        return []

    async def get_teams(self, season_external_id: str) -> list[ProviderTeam]:
        return []

    async def get_fixtures(self, season_external_id: str) -> list[ProviderFixture]:
        return []

    async def get_fixture_statistics(
        self,
        fixture_external_id: str,
    ) -> ProviderFixtureStatistics:
        return ProviderFixtureStatistics(
            fixture_external_id=fixture_external_id,
            metrics={},
        )


def _insert_team(db, name: str) -> int:
    return db.execute(
        text(
            """
            INSERT INTO teams (name, country_code, created_at, updated_at)
            VALUES (:name, 'BRA', now(), now())
            RETURNING id
            """
        ),
        {"name": name},
    ).scalar_one()


def test_versioned_alias_catalog_resolves_known_football_data_labels() -> None:
    validate_provider_team_alias_catalog()
    assert PROVIDER_TEAM_ALIAS_CATALOG_VERSION == "provider-team-aliases-v1"

    assert canonical_names_for_provider_team(
        provider="football-data",
        external_id="team-a",
        labels=("Clube Atlético Mineiro", "Mineiro"),
    ) == ("Atlético-MG",)
    assert canonical_names_for_provider_team(
        provider="football-data",
        external_id="team-b",
        labels=("Club Athletico Paranaense", "Paranaense"),
    ) == ("Athletico-PR",)
    assert canonical_names_for_provider_team(
        provider="football-data",
        external_id="team-c",
        labels=("Clube do Remo",),
    ) == ("Remo",)


def test_known_provider_alias_reuses_canonical_team_and_saves_mapping() -> None:
    provider = FootballDataAliasTestProvider()

    with SessionLocal() as db:
        canonical_id = _insert_team(db, "Atlético-MG")
        service = FootballIngestionService(db, provider)

        internal_id, created = service._upsert_team(
            ProviderTeam(
                external_id="fd-atletico-known",
                name="Clube Atlético Mineiro",
                country_code="BRA",
                aliases=("Mineiro",),
            ),
            strict_reconciliation=True,
        )

        assert internal_id == canonical_id
        assert created is False
        assert db.execute(
            text(
                """
                SELECT internal_id
                FROM external_entity_mappings
                WHERE provider='football-data'
                  AND entity_type='team'
                  AND external_id='fd-atletico-known'
                """
            )
        ).scalar_one() == canonical_id
        assert db.execute(
            text("SELECT count(*) FROM teams WHERE name='Clube Atlético Mineiro'")
        ).scalar_one() == 0
        assert db.execute(
            text(
                """
                SELECT count(*)
                FROM team_aliases
                WHERE normalized_alias='mineiro'
                """
            )
        ).scalar_one() == 0
        db.rollback()


def test_known_provider_alias_uses_canonical_name_when_creation_is_explicitly_allowed() -> None:
    provider = FootballDataAliasTestProvider()

    with SessionLocal() as db:
        service = FootballIngestionService(db, provider)

        internal_id, created = service._upsert_team(
            ProviderTeam(
                external_id="fd-atletico-create",
                name="Clube Atlético Mineiro",
                country_code="BRA",
                aliases=("Mineiro",),
            ),
            strict_reconciliation=False,
        )

        assert created is True
        assert db.execute(
            text("SELECT name FROM teams WHERE id=:id"),
            {"id": internal_id},
        ).scalar_one() == "Atlético-MG"
        assert db.execute(
            text("SELECT count(*) FROM teams WHERE name='Clube Atlético Mineiro'")
        ).scalar_one() == 0
        db.rollback()


def test_unknown_provider_team_remains_fail_closed_in_strict_mode() -> None:
    provider = FootballDataAliasTestProvider()

    with SessionLocal() as db:
        service = FootballIngestionService(db, provider)

        try:
            service._upsert_team(
                ProviderTeam(
                    external_id="fd-unknown",
                    name="Provider Unknown FC",
                    country_code="BRA",
                ),
                strict_reconciliation=True,
            )
        except ValueError as exc:
            assert "Team reconciliation failed" in str(exc)
            assert PROVIDER_TEAM_ALIAS_CATALOG_VERSION in str(exc)
        else:
            raise AssertionError("unknown provider team must fail closed")

        assert db.execute(
            text("SELECT count(*) FROM teams WHERE name='Provider Unknown FC'")
        ).scalar_one() == 0
        db.rollback()


def test_conflicting_provider_alias_evidence_fails_closed() -> None:
    provider = FootballDataAliasTestProvider()

    with SessionLocal() as db:
        _insert_team(db, "Atlético-MG")
        _insert_team(db, "Mineiro")
        service = FootballIngestionService(db, provider)

        try:
            service._upsert_team(
                ProviderTeam(
                    external_id="fd-ambiguous",
                    name="Clube Atlético Mineiro",
                    country_code="BRA",
                    aliases=("Mineiro",),
                ),
                strict_reconciliation=True,
            )
        except ValueError as exc:
            assert "Ambiguous team reconciliation" in str(exc)
        else:
            raise AssertionError("ambiguous provider evidence must fail closed")

        assert db.execute(
            text(
                """
                SELECT count(*)
                FROM external_entity_mappings
                WHERE provider='football-data'
                  AND entity_type='team'
                  AND external_id='fd-ambiguous'
                """
            )
        ).scalar_one() == 0
        db.rollback()


def test_stale_provider_mapping_conflicting_with_catalog_fails_closed() -> None:
    provider = FootballDataAliasTestProvider()

    with SessionLocal() as db:
        wrong_id = _insert_team(db, "Wrong Atlético Mapping")
        db.execute(
            text(
                """
                INSERT INTO external_entity_mappings (
                    entity_type, internal_id, provider, external_id
                ) VALUES (
                    'team', :internal_id, 'football-data', 'fd-stale-atletico'
                )
                """
            ),
            {"internal_id": wrong_id},
        )
        service = FootballIngestionService(db, provider)

        try:
            service._upsert_team(
                ProviderTeam(
                    external_id="fd-stale-atletico",
                    name="Provider Full Atlético Name",
                    country_code="BRA",
                    aliases=("Mineiro",),
                ),
                strict_reconciliation=True,
            )
        except ValueError as exc:
            assert "MAPPED_TEAM_IDENTITY_CONFLICT" in str(exc)
            assert "Atlético-MG" in str(exc)
        else:
            raise AssertionError("stale provider mapping must fail closed")

        db.rollback()


def test_catalog_rejects_conflicting_external_id_ownership() -> None:
    rules = (
        ProviderTeamAliasRule(
            provider="football-data",
            canonical_name="Team A",
            aliases=("Alias A",),
            external_ids=("shared-id",),
        ),
        ProviderTeamAliasRule(
            provider="football-data",
            canonical_name="Team B",
            aliases=("Alias B",),
            external_ids=("shared-id",),
        ),
    )

    with pytest.raises(
        ProviderTeamAliasCatalogError,
        match="DUPLICATE_PROVIDER_TEAM_EXTERNAL_ID",
    ):
        validate_provider_team_alias_catalog(rules)


def test_catalog_rejects_blank_external_id() -> None:
    rules = (
        ProviderTeamAliasRule(
            provider="football-data",
            canonical_name="Team A",
            aliases=(),
            external_ids=("   ",),
        ),
    )

    with pytest.raises(
        ProviderTeamAliasCatalogError,
        match="EMPTY_PROVIDER_TEAM_EXTERNAL_ID",
    ):
        validate_provider_team_alias_catalog(rules)
