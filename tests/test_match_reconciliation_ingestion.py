from datetime import UTC, datetime, timedelta

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
from apps.api.app.services.match_reconciliation import MatchReconciliationError


class ReconciliationProvider(FootballDataProvider):
    name = "bets-11-reconciliation"

    async def get_competitions(self) -> list[ProviderCompetition]:
        raise NotImplementedError

    async def get_seasons(
        self,
        competition_external_id: str,
    ) -> list[ProviderSeason]:
        raise NotImplementedError

    async def get_teams(self, season_external_id: str) -> list[ProviderTeam]:
        raise NotImplementedError

    async def get_fixtures(
        self,
        season_external_id: str,
    ) -> list[ProviderFixture]:
        raise NotImplementedError

    async def get_fixture_statistics(
        self,
        fixture_external_id: str,
    ) -> ProviderFixtureStatistics:
        raise NotImplementedError


def _seed_base(db):
    now = datetime.now(UTC)
    competition_id = db.execute(
        text(
            """
            INSERT INTO competitions (
                name, country_code, competition_type, created_at, updated_at
            ) VALUES (
                'BETS-11 Reconciliation League', 'BRA', 'league', :now, :now
            )
            RETURNING id
            """
        ),
        {"now": now},
    ).scalar_one()
    season_id = db.execute(
        text(
            """
            INSERT INTO seasons (
                competition_id, name, is_current, created_at, updated_at
            ) VALUES (
                :competition_id, '2098', false, :now, :now
            )
            RETURNING id
            """
        ),
        {"competition_id": competition_id, "now": now},
    ).scalar_one()

    team_ids = []
    for name in ("BETS-11 Home", "BETS-11 Away", "BETS-11 Other"):
        team_ids.append(
            db.execute(
                text(
                    """
                    INSERT INTO teams (
                        name, country_code, created_at, updated_at
                    ) VALUES (
                        :name, 'BRA', :now, :now
                    )
                    RETURNING id
                    """
                ),
                {"name": name, "now": now},
            ).scalar_one()
        )
    db.flush()
    return competition_id, season_id, tuple(team_ids)


def _insert_match(
    db,
    *,
    competition_id,
    season_id,
    home_team_id,
    away_team_id,
    kickoff_at,
    stage_id=None,
    round_id=None,
):
    now = datetime.now(UTC)
    return db.execute(
        text(
            """
            INSERT INTO matches (
                competition_id, season_id, stage_id, round_id,
                home_team_id, away_team_id, kickoff_at, status,
                created_at, updated_at
            ) VALUES (
                :competition_id, :season_id, :stage_id, :round_id,
                :home_team_id, :away_team_id, :kickoff_at, 'scheduled',
                :now, :now
            )
            RETURNING id
            """
        ),
        {
            "competition_id": competition_id,
            "season_id": season_id,
            "stage_id": stage_id,
            "round_id": round_id,
            "home_team_id": home_team_id,
            "away_team_id": away_team_id,
            "kickoff_at": kickoff_at,
            "now": now,
        },
    ).scalar_one()


def test_repeated_fixture_reconciles_by_round_after_large_schedule_change():
    with SessionLocal() as db:
        competition_id, season_id, teams = _seed_base(db)
        home_id, away_id, _ = teams
        service = FootballIngestionService(db, ReconciliationProvider())

        stage_id = service._upsert_stage(season_id, "Regular Season")
        round_1 = service._upsert_round(season_id, 1, stage_id=stage_id)
        round_20 = service._upsert_round(season_id, 20, stage_id=stage_id)

        first_id = _insert_match(
            db,
            competition_id=competition_id,
            season_id=season_id,
            home_team_id=home_id,
            away_team_id=away_id,
            kickoff_at=datetime(2098, 2, 1, 19, tzinfo=UTC),
            stage_id=stage_id,
            round_id=round_1,
        )
        second_id = _insert_match(
            db,
            competition_id=competition_id,
            season_id=season_id,
            home_team_id=home_id,
            away_team_id=away_id,
            kickoff_at=datetime(2098, 8, 1, 19, tzinfo=UTC),
            stage_id=stage_id,
            round_id=round_20,
        )

        moved_kickoff = datetime(2098, 8, 8, 21, tzinfo=UTC)
        fixture = ProviderFixture(
            external_id="provider-second-leg",
            season_external_id="2098",
            home_team_external_id="home",
            away_team_external_id="away",
            kickoff_at=moved_kickoff,
            status="postponed",
            round_number=20,
            stage="REGULAR SEASON",
        )
        internal_id, created = service._upsert_match(
            competition_id=competition_id,
            season_id=season_id,
            stage_id=stage_id,
            round_id=round_20,
            home_team_id=home_id,
            away_team_id=away_id,
            fixture=fixture,
        )

        assert created is False
        assert internal_id == second_id
        assert internal_id != first_id
        assert db.execute(
            text(
                """
                SELECT count(*)
                FROM matches
                WHERE season_id=:season_id
                  AND home_team_id=:home_team_id
                  AND away_team_id=:away_team_id
                """
            ),
            {
                "season_id": season_id,
                "home_team_id": home_id,
                "away_team_id": away_id,
            },
        ).scalar_one() == 2
        row = db.execute(
            text("SELECT kickoff_at, status FROM matches WHERE id=:id"),
            {"id": second_id},
        ).one()
        assert row.kickoff_at == moved_kickoff
        assert row.status == "postponed"
        assert db.execute(
            text(
                """
                SELECT internal_id
                FROM external_entity_mappings
                WHERE provider=:provider
                  AND entity_type='match'
                  AND external_id='provider-second-leg'
                """
            ),
            {"provider": ReconciliationProvider.name},
        ).scalar_one() == second_id
        db.rollback()


def test_ambiguous_repeated_fixture_blocks_without_creating_duplicate():
    with SessionLocal() as db:
        competition_id, season_id, teams = _seed_base(db)
        home_id, away_id, _ = teams
        service = FootballIngestionService(db, ReconciliationProvider())
        kickoff = datetime(2098, 5, 1, 19, tzinfo=UTC)
        first_id = _insert_match(
            db,
            competition_id=competition_id,
            season_id=season_id,
            home_team_id=home_id,
            away_team_id=away_id,
            kickoff_at=kickoff,
        )
        second_id = _insert_match(
            db,
            competition_id=competition_id,
            season_id=season_id,
            home_team_id=home_id,
            away_team_id=away_id,
            kickoff_at=kickoff + timedelta(hours=24),
        )
        fixture = ProviderFixture(
            external_id="ambiguous-provider-fixture",
            season_external_id="2098",
            home_team_external_id="home",
            away_team_external_id="away",
            kickoff_at=kickoff + timedelta(hours=12),
            status="scheduled",
        )

        with pytest.raises(MatchReconciliationError, match="AMBIGUOUS"):
            service._upsert_match(
                competition_id=competition_id,
                season_id=season_id,
                stage_id=None,
                round_id=None,
                home_team_id=home_id,
                away_team_id=away_id,
                fixture=fixture,
            )

        assert db.execute(
            text(
                """
                SELECT array_agg(id ORDER BY id)
                FROM matches
                WHERE season_id=:season_id
                  AND home_team_id=:home_team_id
                  AND away_team_id=:away_team_id
                """
            ),
            {
                "season_id": season_id,
                "home_team_id": home_id,
                "away_team_id": away_id,
            },
        ).scalar_one() == [first_id, second_id]
        assert db.execute(
            text(
                """
                SELECT count(*)
                FROM external_entity_mappings
                WHERE provider=:provider
                  AND entity_type='match'
                  AND external_id='ambiguous-provider-fixture'
                """
            ),
            {"provider": ReconciliationProvider.name},
        ).scalar_one() == 0
        db.rollback()


def test_existing_mapping_cannot_move_to_different_canonical_fixture():
    with SessionLocal() as db:
        competition_id, season_id, teams = _seed_base(db)
        home_id, away_id, other_id = teams
        service = FootballIngestionService(db, ReconciliationProvider())
        match_id = _insert_match(
            db,
            competition_id=competition_id,
            season_id=season_id,
            home_team_id=home_id,
            away_team_id=away_id,
            kickoff_at=datetime(2098, 6, 1, 19, tzinfo=UTC),
        )
        db.execute(
            text(
                """
                INSERT INTO external_entity_mappings (
                    entity_type, internal_id, provider, external_id
                ) VALUES (
                    'match', :internal_id, :provider, 'mapped-fixture'
                )
                """
            ),
            {
                "internal_id": match_id,
                "provider": ReconciliationProvider.name,
            },
        )

        conflicting = ProviderFixture(
            external_id="mapped-fixture",
            season_external_id="2098",
            home_team_external_id="home",
            away_team_external_id="other",
            kickoff_at=datetime(2098, 6, 1, 19, tzinfo=UTC),
            status="scheduled",
        )
        with pytest.raises(ValueError, match="MAPPED_MATCH_IDENTITY_CONFLICT"):
            service._upsert_match(
                competition_id=competition_id,
                season_id=season_id,
                stage_id=None,
                round_id=None,
                home_team_id=home_id,
                away_team_id=other_id,
                fixture=conflicting,
            )

        persisted = db.execute(
            text(
                """
                SELECT home_team_id, away_team_id
                FROM matches
                WHERE id=:match_id
                """
            ),
            {"match_id": match_id},
        ).one()
        assert persisted.home_team_id == home_id
        assert persisted.away_team_id == away_id
        db.rollback()


def test_same_round_number_can_exist_in_distinct_stages():
    with SessionLocal() as db:
        _, season_id, _ = _seed_base(db)
        service = FootballIngestionService(db, ReconciliationProvider())

        group_stage = service._upsert_stage(season_id, "Group Stage")
        final_stage = service._upsert_stage(season_id, "Final")
        group_round = service._upsert_round(
            season_id,
            1,
            stage_id=group_stage,
        )
        final_round = service._upsert_round(
            season_id,
            1,
            stage_id=final_stage,
        )

        assert group_round != final_round
        assert db.execute(
            text(
                """
                SELECT count(*)
                FROM rounds
                WHERE season_id=:season_id AND round_number=1
                """
            ),
            {"season_id": season_id},
        ).scalar_one() == 2
        db.rollback()
