import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from apps.api.app.providers.base import FootballDataProvider
from apps.api.app.providers.contracts import (
    ProviderCompetition,
    ProviderFixture,
    ProviderSeason,
    ProviderTeam,
)


@dataclass(slots=True)
class SyncResult:
    provider: str
    competition_external_id: str
    season_external_id: str
    competitions_created: int = 0
    competitions_updated: int = 0
    seasons_created: int = 0
    seasons_updated: int = 0
    teams_created: int = 0
    teams_updated: int = 0
    matches_created: int = 0
    matches_updated: int = 0

    @property
    def records_created(self) -> int:
        return (
            self.competitions_created
            + self.seasons_created
            + self.teams_created
            + self.matches_created
        )

    @property
    def records_updated(self) -> int:
        return (
            self.competitions_updated
            + self.seasons_updated
            + self.teams_updated
            + self.matches_updated
        )


class FootballIngestionService:
    def __init__(self, db: Session, provider: FootballDataProvider) -> None:
        self.db = db
        self.provider = provider

    async def sync_season(
        self,
        competition_external_id: str,
        season_external_id: str,
        *,
        include_fixtures: bool = True,
        fixture_date_from: date | None = None,
        fixture_date_to: date | None = None,
        strict_team_reconciliation: bool = False,
    ) -> SyncResult:
        if (fixture_date_from is None) != (fixture_date_to is None):
            raise ValueError("fixture_date_from and fixture_date_to must be provided together")
        if (
            fixture_date_from is not None
            and fixture_date_to is not None
            and fixture_date_from > fixture_date_to
        ):
            raise ValueError("fixture_date_from cannot be after fixture_date_to")

        run_id = self._start_sync_run(
            resource="fixture_window" if fixture_date_from else "season",
            metadata={
                "competition_external_id": competition_external_id,
                "season_external_id": season_external_id,
                "include_fixtures": include_fixtures,
                "fixture_date_from": fixture_date_from,
                "fixture_date_to": fixture_date_to,
                "strict_team_reconciliation": strict_team_reconciliation,
            },
        )
        self.db.commit()

        result = SyncResult(
            provider=self.provider.name,
            competition_external_id=competition_external_id,
            season_external_id=season_external_id,
        )

        try:
            competitions = await self.provider.get_competitions()
            competition = self._find_competition(competitions, competition_external_id)
            competition_id, created = self._upsert_competition(competition)
            if created:
                result.competitions_created += 1
            else:
                result.competitions_updated += 1

            seasons = await self.provider.get_seasons(competition_external_id)
            season = self._find_season(seasons, season_external_id)
            season_id, created = self._upsert_season(competition_id, season)
            if created:
                result.seasons_created += 1
            else:
                result.seasons_updated += 1

            teams = await self.provider.get_teams(season_external_id)
            team_ids: dict[str, int] = {}
            for team in teams:
                team_id, created = self._upsert_team(
                    team,
                    strict_reconciliation=strict_team_reconciliation,
                )
                team_ids[team.external_id] = team_id
                if created:
                    result.teams_created += 1
                else:
                    result.teams_updated += 1

            fixtures: list[ProviderFixture] = []
            if include_fixtures:
                if fixture_date_from is not None and fixture_date_to is not None:
                    fixtures = await self.provider.get_fixtures_between(
                        season_external_id,
                        fixture_date_from,
                        fixture_date_to,
                    )
                else:
                    fixtures = await self.provider.get_fixtures(season_external_id)

                for fixture in fixtures:
                    home_team_id = team_ids.get(fixture.home_team_external_id)
                    away_team_id = team_ids.get(fixture.away_team_external_id)
                    if home_team_id is None or away_team_id is None:
                        raise ValueError(
                            f"Fixture {fixture.external_id} references teams not returned for season"
                        )

                    round_id = None
                    if fixture.round_number is not None:
                        round_id = self._upsert_round(season_id, fixture.round_number)

                    _, created = self._upsert_match(
                        competition_id=competition_id,
                        season_id=season_id,
                        round_id=round_id,
                        home_team_id=home_team_id,
                        away_team_id=away_team_id,
                        fixture=fixture,
                    )
                    if created:
                        result.matches_created += 1
                    else:
                        result.matches_updated += 1

            self._finish_sync_run(
                run_id=run_id,
                status="success",
                records_received=2 + len(teams) + len(fixtures),
                records_created=result.records_created,
                records_updated=result.records_updated,
            )
            self.db.commit()
            return result
        except Exception as exc:
            self.db.rollback()
            self._finish_sync_run(
                run_id=run_id,
                status="failed",
                error=str(exc),
            )
            self.db.commit()
            raise

    @staticmethod
    def _find_competition(
        competitions: list[ProviderCompetition],
        external_id: str,
    ) -> ProviderCompetition:
        for competition in competitions:
            if competition.external_id == external_id:
                return competition
        raise ValueError(f"Competition '{external_id}' was not returned by provider")

    @staticmethod
    def _find_season(seasons: list[ProviderSeason], external_id: str) -> ProviderSeason:
        for season in seasons:
            if season.external_id == external_id:
                return season
        raise ValueError(f"Season '{external_id}' was not returned by provider")

    def _mapping(self, entity_type: str, external_id: str) -> int | None:
        return self.db.execute(
            text(
                """
                SELECT internal_id
                FROM external_entity_mappings
                WHERE provider = :provider
                  AND entity_type = :entity_type
                  AND external_id = :external_id
                """
            ),
            {
                "provider": self.provider.name,
                "entity_type": entity_type,
                "external_id": external_id,
            },
        ).scalar_one_or_none()

    def _save_mapping(self, entity_type: str, internal_id: int, external_id: str) -> None:
        self.db.execute(
            text(
                """
                INSERT INTO external_entity_mappings (
                    entity_type, internal_id, provider, external_id
                )
                VALUES (:entity_type, :internal_id, :provider, :external_id)
                ON CONFLICT (provider, entity_type, external_id)
                DO UPDATE SET internal_id = EXCLUDED.internal_id
                """
            ),
            {
                "entity_type": entity_type,
                "internal_id": internal_id,
                "provider": self.provider.name,
                "external_id": external_id,
            },
        )

    def _save_raw(self, resource_type: str, external_id: str, payload: dict | None) -> None:
        if payload is None:
            return
        self.db.execute(
            text(
                """
                INSERT INTO provider_raw_payloads (
                    provider, resource_type, external_id, payload, fetched_at
                )
                VALUES (
                    :provider, :resource_type, :external_id,
                    CAST(:payload AS jsonb), :fetched_at
                )
                """
            ),
            {
                "provider": self.provider.name,
                "resource_type": resource_type,
                "external_id": external_id,
                "payload": json.dumps(payload, default=str),
                "fetched_at": datetime.now(UTC),
            },
        )

    def _upsert_competition(self, item: ProviderCompetition) -> tuple[int, bool]:
        now = datetime.now(UTC)
        internal_id = self._mapping("competition", item.external_id)
        created = False

        if internal_id is None:
            internal_id = self._reconcile_competition(item)
            if internal_id is not None:
                self._save_mapping("competition", internal_id, item.external_id)

        if internal_id is None:
            internal_id = self.db.execute(
                text(
                    """
                    INSERT INTO competitions (
                        name, country_code, competition_type, created_at, updated_at
                    )
                    VALUES (:name, :country_code, 'league', :now, :now)
                    RETURNING id
                    """
                ),
                {"name": item.name, "country_code": item.country_code, "now": now},
            ).scalar_one()
            self._save_mapping("competition", internal_id, item.external_id)
            created = True
        else:
            self.db.execute(
                text(
                    """
                    UPDATE competitions
                    SET country_code = COALESCE(country_code, :country_code),
                        updated_at = :now
                    WHERE id = :internal_id
                    """
                ),
                {
                    "country_code": item.country_code,
                    "now": now,
                    "internal_id": internal_id,
                },
            )

        self._save_raw("competition", item.external_id, item.raw)
        return internal_id, created

    def _upsert_season(self, competition_id: int, item: ProviderSeason) -> tuple[int, bool]:
        now = datetime.now(UTC)
        internal_id = self._mapping("season", item.external_id)
        created = False
        start_date = item.start_date.date() if item.start_date else None
        end_date = item.end_date.date() if item.end_date else None

        if internal_id is None:
            internal_id = self.db.execute(
                text(
                    """
                    SELECT id
                    FROM seasons
                    WHERE competition_id = :competition_id
                      AND name = :name
                    ORDER BY id
                    LIMIT 1
                    """
                ),
                {"competition_id": competition_id, "name": item.name},
            ).scalar_one_or_none()
            if internal_id is not None:
                self._save_mapping("season", internal_id, item.external_id)

        if internal_id is None:
            internal_id = self.db.execute(
                text(
                    """
                    INSERT INTO seasons (
                        competition_id, name, start_date, end_date, is_current,
                        created_at, updated_at
                    )
                    VALUES (
                        :competition_id, :name, :start_date, :end_date, false,
                        :now, :now
                    )
                    RETURNING id
                    """
                ),
                {
                    "competition_id": competition_id,
                    "name": item.name,
                    "start_date": start_date,
                    "end_date": end_date,
                    "now": now,
                },
            ).scalar_one()
            self._save_mapping("season", internal_id, item.external_id)
            created = True
        else:
            self.db.execute(
                text(
                    """
                    UPDATE seasons
                    SET competition_id = :competition_id,
                        start_date = COALESCE(:start_date, start_date),
                        end_date = COALESCE(:end_date, end_date),
                        updated_at = :now
                    WHERE id = :internal_id
                    """
                ),
                {
                    "competition_id": competition_id,
                    "start_date": start_date,
                    "end_date": end_date,
                    "now": now,
                    "internal_id": internal_id,
                },
            )

        self._save_raw("season", item.external_id, item.raw)
        return internal_id, created

    def _upsert_team(
        self,
        item: ProviderTeam,
        *,
        strict_reconciliation: bool = False,
    ) -> tuple[int, bool]:
        now = datetime.now(UTC)
        internal_id = self._mapping("team", item.external_id)
        created = False

        if internal_id is None:
            internal_id = self._reconcile_team(item.name)
            if internal_id is not None:
                self._save_mapping("team", internal_id, item.external_id)

        if internal_id is None and strict_reconciliation:
            raise ValueError(
                "Team reconciliation failed for provider team "
                f"'{item.name}' ({item.external_id}). Add a canonical team alias or mapping first."
            )

        if internal_id is None:
            internal_id = self.db.execute(
                text(
                    """
                    INSERT INTO teams (
                        name, short_name, country_code, created_at, updated_at
                    )
                    VALUES (:name, NULL, :country_code, :now, :now)
                    RETURNING id
                    """
                ),
                {"name": item.name, "country_code": item.country_code, "now": now},
            ).scalar_one()
            self._save_mapping("team", internal_id, item.external_id)
            normalized = self._normalize(item.name)
            if normalized:
                self.db.execute(
                    text(
                        """
                        INSERT INTO team_aliases (team_id, alias, normalized_alias)
                        VALUES (:team_id, :alias, :normalized_alias)
                        ON CONFLICT (normalized_alias) DO NOTHING
                        """
                    ),
                    {
                        "team_id": internal_id,
                        "alias": item.name,
                        "normalized_alias": normalized,
                    },
                )
            created = True
        else:
            self.db.execute(
                text(
                    """
                    UPDATE teams
                    SET country_code = COALESCE(country_code, :country_code),
                        updated_at = :now
                    WHERE id = :internal_id
                    """
                ),
                {
                    "country_code": item.country_code,
                    "now": now,
                    "internal_id": internal_id,
                },
            )

        self._save_raw("team", item.external_id, item.raw)
        return internal_id, created

    def _upsert_round(self, season_id: int, round_number: int) -> int:
        existing = self.db.execute(
            text(
                """
                SELECT id
                FROM rounds
                WHERE season_id = :season_id
                  AND round_number = :round_number
                ORDER BY id
                LIMIT 1
                """
            ),
            {"season_id": season_id, "round_number": round_number},
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        return self.db.execute(
            text(
                """
                INSERT INTO rounds (season_id, name, round_number)
                VALUES (:season_id, :name, :round_number)
                RETURNING id
                """
            ),
            {
                "season_id": season_id,
                "name": f"Rodada {round_number}",
                "round_number": round_number,
            },
        ).scalar_one()

    def _upsert_match(
        self,
        competition_id: int,
        season_id: int,
        round_id: int | None,
        home_team_id: int,
        away_team_id: int,
        fixture: ProviderFixture,
    ) -> tuple[int, bool]:
        now = datetime.now(UTC)
        internal_id = self._mapping("match", fixture.external_id)
        created = False

        if internal_id is None:
            candidates = self.db.execute(
                text(
                    """
                    SELECT id
                    FROM matches
                    WHERE season_id = :season_id
                      AND home_team_id = :home_team_id
                      AND away_team_id = :away_team_id
                    ORDER BY id
                    """
                ),
                {
                    "season_id": season_id,
                    "home_team_id": home_team_id,
                    "away_team_id": away_team_id,
                },
            ).scalars().all()
            if len(candidates) == 1:
                internal_id = candidates[0]
                self._save_mapping("match", internal_id, fixture.external_id)

        if internal_id is None:
            internal_id = self.db.execute(
                text(
                    """
                    INSERT INTO matches (
                        competition_id, season_id, round_id,
                        home_team_id, away_team_id,
                        kickoff_at, status, home_score, away_score,
                        provider_updated_at, last_synced_at,
                        created_at, updated_at
                    )
                    VALUES (
                        :competition_id, :season_id, :round_id,
                        :home_team_id, :away_team_id,
                        :kickoff_at, :status, :home_score, :away_score,
                        :provider_updated_at, :last_synced_at,
                        :now, :now
                    )
                    RETURNING id
                    """
                ),
                {
                    "competition_id": competition_id,
                    "season_id": season_id,
                    "round_id": round_id,
                    "home_team_id": home_team_id,
                    "away_team_id": away_team_id,
                    "kickoff_at": fixture.kickoff_at,
                    "status": fixture.status,
                    "home_score": fixture.home_score,
                    "away_score": fixture.away_score,
                    "provider_updated_at": fixture.provider_updated_at,
                    "last_synced_at": now,
                    "now": now,
                },
            ).scalar_one()
            self._save_mapping("match", internal_id, fixture.external_id)
            created = True
        else:
            self.db.execute(
                text(
                    """
                    UPDATE matches
                    SET competition_id = :competition_id,
                        season_id = :season_id,
                        round_id = COALESCE(:round_id, round_id),
                        home_team_id = :home_team_id,
                        away_team_id = :away_team_id,
                        kickoff_at = :kickoff_at,
                        status = :status,
                        home_score = COALESCE(:home_score, home_score),
                        away_score = COALESCE(:away_score, away_score),
                        provider_updated_at = COALESCE(:provider_updated_at, provider_updated_at),
                        last_synced_at = :last_synced_at,
                        updated_at = :now
                    WHERE id = :internal_id
                    """
                ),
                {
                    "competition_id": competition_id,
                    "season_id": season_id,
                    "round_id": round_id,
                    "home_team_id": home_team_id,
                    "away_team_id": away_team_id,
                    "kickoff_at": fixture.kickoff_at,
                    "status": fixture.status,
                    "home_score": fixture.home_score,
                    "away_score": fixture.away_score,
                    "provider_updated_at": fixture.provider_updated_at,
                    "last_synced_at": now,
                    "now": now,
                    "internal_id": internal_id,
                },
            )

        self._save_raw("match", fixture.external_id, fixture.raw)
        return internal_id, created

    def _reconcile_competition(self, item: ProviderCompetition) -> int | None:
        target = self._competition_key(item.name)
        candidates = [
            row.id
            for row in self.db.execute(
                text("SELECT id, name, country_code FROM competitions")
            ).all()
            if self._competition_key(row.name) == target
            and (
                item.country_code is None
                or row.country_code is None
                or row.country_code == item.country_code
            )
        ]
        if len(candidates) > 1:
            raise ValueError(
                f"Ambiguous competition reconciliation for '{item.name}': {candidates}"
            )
        return candidates[0] if candidates else None

    def _reconcile_team(self, name: str) -> int | None:
        target = self._normalize(name)
        candidates: set[int] = set()

        for row in self.db.execute(text("SELECT id, name FROM teams")).all():
            if self._normalize(row.name) == target:
                candidates.add(row.id)

        for row in self.db.execute(
            text("SELECT team_id, alias FROM team_aliases")
        ).all():
            if self._normalize(row.alias) == target:
                candidates.add(row.team_id)

        if len(candidates) > 1:
            raise ValueError(
                f"Ambiguous team reconciliation for '{name}': {sorted(candidates)}"
            )
        return next(iter(candidates)) if candidates else None

    @classmethod
    def _competition_key(cls, value: str) -> str:
        normalized = cls._normalize(value)
        return normalized.replace("campeonatobrasileiro", "brasileirao")

    @staticmethod
    def _normalize(value: str) -> str:
        ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
        return re.sub(r"[^a-z0-9]+", "", ascii_value.lower())

    def _start_sync_run(self, resource: str, metadata: dict) -> int:
        return self.db.execute(
            text(
                """
                INSERT INTO provider_sync_runs (
                    provider, resource, status, metadata
                )
                VALUES (
                    :provider, :resource, 'running', CAST(:metadata AS jsonb)
                )
                RETURNING id
                """
            ),
            {
                "provider": self.provider.name,
                "resource": resource,
                "metadata": json.dumps(metadata, default=str),
            },
        ).scalar_one()

    def _finish_sync_run(
        self,
        run_id: int,
        status: str,
        records_received: int = 0,
        records_created: int = 0,
        records_updated: int = 0,
        error: str | None = None,
    ) -> None:
        self.db.execute(
            text(
                """
                UPDATE provider_sync_runs
                SET finished_at = :finished_at,
                    status = :status,
                    records_received = :records_received,
                    records_created = :records_created,
                    records_updated = :records_updated,
                    error = :error
                WHERE id = :run_id
                """
            ),
            {
                "finished_at": datetime.now(UTC),
                "status": status,
                "records_received": records_received,
                "records_created": records_created,
                "records_updated": records_updated,
                "error": error,
                "run_id": run_id,
            },
        )
