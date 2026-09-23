import json
import logging
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
from apps.api.app.providers.team_aliases import (
    PROVIDER_TEAM_ALIAS_CATALOG_VERSION,
    canonical_names_for_provider_team,
)
from apps.api.app.services.match_reconciliation import (
    MatchCandidate,
    MatchReconciliationError,
    resolve_match_candidate,
)

logger = logging.getLogger(__name__)


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

                    stage_id = None
                    if fixture.stage:
                        stage_id = self._upsert_stage(season_id, fixture.stage)

                    round_id = None
                    if fixture.round_number is not None:
                        round_id = self._upsert_round(
                            season_id,
                            fixture.round_number,
                            stage_id=stage_id,
                        )

                    _, created = self._upsert_match(
                        competition_id=competition_id,
                        season_id=season_id,
                        stage_id=stage_id,
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

    def _provider_competition_format(
        self,
        item: ProviderCompetition,
    ) -> tuple[str, str | None]:
        raw_type = (item.competition_type or "").strip()
        if not raw_type:
            return "unknown", None

        normalized = self._normalize(raw_type)
        if normalized in {"league", "leaguepoints", "roundrobin"}:
            canonical = "league"
        elif normalized in {"cup", "knockout", "playoff", "playoffs"}:
            canonical = "cup"
        else:
            canonical = "unknown"

        return canonical, f"provider:{self.provider.name}"

    def _upsert_competition(self, item: ProviderCompetition) -> tuple[int, bool]:
        now = datetime.now(UTC)
        internal_id = self._mapping("competition", item.external_id)
        created = False
        competition_type, type_source = self._provider_competition_format(item)

        if internal_id is None:
            internal_id = self._reconcile_competition(item)
            if internal_id is not None:
                self._save_mapping("competition", internal_id, item.external_id)

        if internal_id is None:
            internal_id = self.db.execute(
                text(
                    """
                    INSERT INTO competitions (
                        name, country_code, competition_type,
                        competition_type_source, created_at, updated_at
                    )
                    VALUES (
                        :name, :country_code, :competition_type,
                        :competition_type_source, :now, :now
                    )
                    RETURNING id
                    """
                ),
                {
                    "name": item.name,
                    "country_code": item.country_code,
                    "competition_type": competition_type,
                    "competition_type_source": type_source,
                    "now": now,
                },
            ).scalar_one()
            self._save_mapping("competition", internal_id, item.external_id)
            created = True
        else:
            if type_source is None:
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
            else:
                self.db.execute(
                    text(
                        """
                        UPDATE competitions
                        SET country_code = COALESCE(country_code, :country_code),
                            competition_type = :competition_type,
                            competition_type_source = :competition_type_source,
                            updated_at = :now
                        WHERE id = :internal_id
                        """
                    ),
                    {
                        "country_code": item.country_code,
                        "competition_type": competition_type,
                        "competition_type_source": type_source,
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
        canonical_names = self._provider_team_canonical_names(item)
        canonical_name = canonical_names[0] if canonical_names else None

        if internal_id is not None and canonical_name is not None:
            canonical_candidates = self._team_candidate_ids(canonical_name)
            if canonical_candidates and internal_id not in canonical_candidates:
                raise ValueError(
                    "MAPPED_TEAM_IDENTITY_CONFLICT:"
                    f"provider={self.provider.name},external_id={item.external_id},"
                    f"internal_id={internal_id},canonical_name={canonical_name},"
                    f"candidate_ids={sorted(canonical_candidates)}"
                )

        if internal_id is None:
            internal_id = self._reconcile_provider_team(item, canonical_names)
            if internal_id is not None:
                self._save_mapping("team", internal_id, item.external_id)

        if internal_id is None and strict_reconciliation:
            raise ValueError(
                "Team reconciliation failed for provider team "
                f"'{item.name}' ({item.external_id}). "
                f"Alias catalog={PROVIDER_TEAM_ALIAS_CATALOG_VERSION}. "
                "Add a versioned provider alias/mapping before syncing."
            )

        if internal_id is None:
            creation_name = canonical_name or item.name
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
                {
                    "name": creation_name,
                    "country_code": item.country_code,
                    "now": now,
                },
            ).scalar_one()
            self._save_mapping("team", internal_id, item.external_id)
            normalized = self._normalize(creation_name)
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
                        "alias": creation_name,
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

    def _upsert_stage(self, season_id: int, stage_name: str) -> int:
        name = stage_name.strip()
        if not name:
            raise ValueError("Fixture stage cannot be blank")

        target = self._normalize(name)
        candidates = [
            row.id
            for row in self.db.execute(
                text(
                    """
                    SELECT id, name
                    FROM stages
                    WHERE season_id = :season_id
                    ORDER BY id
                    """
                ),
                {"season_id": season_id},
            ).all()
            if self._normalize(row.name) == target
        ]
        if len(candidates) > 1:
            raise ValueError(
                f"Ambiguous stage reconciliation for '{name}': {candidates}"
            )
        if candidates:
            return candidates[0]

        return self.db.execute(
            text(
                """
                INSERT INTO stages (
                    season_id, name, stage_type, sort_order
                )
                VALUES (:season_id, :name, NULL, 0)
                RETURNING id
                """
            ),
            {"season_id": season_id, "name": name},
        ).scalar_one()

    def _upsert_round(
        self,
        season_id: int,
        round_number: int,
        *,
        stage_id: int | None = None,
    ) -> int:
        candidates = self.db.execute(
            text(
                """
                SELECT id, stage_id
                FROM rounds
                WHERE season_id = :season_id
                  AND round_number = :round_number
                ORDER BY id
                """
            ),
            {"season_id": season_id, "round_number": round_number},
        ).all()

        if stage_id is not None:
            same_stage = [row.id for row in candidates if row.stage_id == stage_id]
            if len(same_stage) > 1:
                raise ValueError(
                    "Ambiguous round reconciliation for "
                    f"season={season_id}, round={round_number}, stage={stage_id}: "
                    f"{same_stage}"
                )
            if same_stage:
                return same_stage[0]

            stage_unknown = [row.id for row in candidates if row.stage_id is None]
            if len(stage_unknown) == 1:
                self.db.execute(
                    text(
                        """
                        UPDATE rounds
                        SET stage_id = :stage_id
                        WHERE id = :round_id
                        """
                    ),
                    {"stage_id": stage_id, "round_id": stage_unknown[0]},
                )
                return stage_unknown[0]
            if len(stage_unknown) > 1:
                raise ValueError(
                    "Ambiguous round reconciliation for "
                    f"season={season_id}, round={round_number}: {stage_unknown}"
                )
        else:
            if len(candidates) == 1:
                return candidates[0].id
            if len(candidates) > 1:
                raise ValueError(
                    "Ambiguous round reconciliation for "
                    f"season={season_id}, round={round_number}: "
                    f"{[row.id for row in candidates]}"
                )

        return self.db.execute(
            text(
                """
                INSERT INTO rounds (
                    season_id, stage_id, name, round_number
                )
                VALUES (:season_id, :stage_id, :name, :round_number)
                RETURNING id
                """
            ),
            {
                "season_id": season_id,
                "stage_id": stage_id,
                "name": f"Rodada {round_number}",
                "round_number": round_number,
            },
        ).scalar_one()

    def _match_candidates(
        self,
        *,
        season_id: int,
        home_team_id: int,
        away_team_id: int,
    ) -> tuple[MatchCandidate, ...]:
        rows = self.db.execute(
            text(
                """
                SELECT
                    m.id,
                    m.kickoff_at,
                    r.round_number,
                    COALESCE(match_stage.name, round_stage.name) AS stage_name
                FROM matches m
                LEFT JOIN rounds r ON r.id = m.round_id
                LEFT JOIN stages match_stage ON match_stage.id = m.stage_id
                LEFT JOIN stages round_stage ON round_stage.id = r.stage_id
                WHERE m.season_id = :season_id
                  AND m.home_team_id = :home_team_id
                  AND m.away_team_id = :away_team_id
                ORDER BY m.kickoff_at, m.id
                """
            ),
            {
                "season_id": season_id,
                "home_team_id": home_team_id,
                "away_team_id": away_team_id,
            },
        ).all()
        return tuple(
            MatchCandidate(
                match_id=int(row.id),
                kickoff_at=row.kickoff_at,
                round_number=row.round_number,
                stage_name=row.stage_name,
            )
            for row in rows
        )

    def _validate_mapped_match_identity(
        self,
        *,
        internal_id: int,
        competition_id: int,
        season_id: int,
        home_team_id: int,
        away_team_id: int,
        fixture_external_id: str,
    ) -> None:
        row = self.db.execute(
            text(
                """
                SELECT competition_id, season_id, home_team_id, away_team_id
                FROM matches
                WHERE id = :internal_id
                """
            ),
            {"internal_id": internal_id},
        ).one_or_none()
        if row is None:
            raise ValueError(
                "STALE_MATCH_MAPPING:"
                f"provider={self.provider.name}, external_id={fixture_external_id}, "
                f"internal_id={internal_id}"
            )
        expected = (
            competition_id,
            season_id,
            home_team_id,
            away_team_id,
        )
        current = (
            row.competition_id,
            row.season_id,
            row.home_team_id,
            row.away_team_id,
        )
        if current != expected:
            raise ValueError(
                "MAPPED_MATCH_IDENTITY_CONFLICT:"
                f"provider={self.provider.name}, external_id={fixture_external_id}, "
                f"internal_id={internal_id}, current={current}, expected={expected}"
            )

    def _ensure_match_mapping_available(
        self,
        *,
        internal_id: int,
        external_id: str,
    ) -> None:
        existing_external_id = self.db.execute(
            text(
                """
                SELECT external_id
                FROM external_entity_mappings
                WHERE provider = :provider
                  AND entity_type = 'match'
                  AND internal_id = :internal_id
                """
            ),
            {
                "provider": self.provider.name,
                "internal_id": internal_id,
            },
        ).scalar_one_or_none()
        if (
            existing_external_id is not None
            and existing_external_id != external_id
        ):
            raise ValueError(
                "MATCH_ALREADY_MAPPED_FOR_PROVIDER:"
                f"provider={self.provider.name}, internal_id={internal_id}, "
                f"existing_external_id={existing_external_id}, "
                f"incoming_external_id={external_id}"
            )

    def _upsert_match(
        self,
        competition_id: int,
        season_id: int,
        stage_id: int | None,
        round_id: int | None,
        home_team_id: int,
        away_team_id: int,
        fixture: ProviderFixture,
    ) -> tuple[int, bool]:
        if (
            fixture.kickoff_at.tzinfo is None
            or fixture.kickoff_at.utcoffset() is None
        ):
            raise ValueError("Fixture kickoff_at must be timezone-aware")

        now = datetime.now(UTC)
        internal_id = self._mapping("match", fixture.external_id)
        created = False

        if internal_id is not None:
            self._validate_mapped_match_identity(
                internal_id=internal_id,
                competition_id=competition_id,
                season_id=season_id,
                home_team_id=home_team_id,
                away_team_id=away_team_id,
                fixture_external_id=fixture.external_id,
            )
        else:
            candidates = self._match_candidates(
                season_id=season_id,
                home_team_id=home_team_id,
                away_team_id=away_team_id,
            )
            try:
                resolution = resolve_match_candidate(
                    candidates=candidates,
                    kickoff_at=fixture.kickoff_at,
                    round_number=fixture.round_number,
                    stage_name=fixture.stage,
                )
            except MatchReconciliationError as exc:
                logger.warning(
                    "match_reconciliation_blocked",
                    extra={
                        "provider": self.provider.name,
                        "external_id": fixture.external_id,
                        "season_id": season_id,
                        "home_team_id": home_team_id,
                        "away_team_id": away_team_id,
                        "kickoff_at": fixture.kickoff_at.isoformat(),
                        "round_number": fixture.round_number,
                        "stage": fixture.stage,
                        "candidate_ids": list(exc.candidate_ids),
                        "reason": exc.reason.value,
                    },
                )
                raise

            internal_id = resolution.match_id
            logger.info(
                "match_reconciliation_resolved",
                extra={
                    "provider": self.provider.name,
                    "external_id": fixture.external_id,
                    "season_id": season_id,
                    "home_team_id": home_team_id,
                    "away_team_id": away_team_id,
                    "kickoff_at": fixture.kickoff_at.isoformat(),
                    "round_number": fixture.round_number,
                    "stage": fixture.stage,
                    "candidate_ids": list(resolution.candidate_ids),
                    "match_id": internal_id,
                    "reason": resolution.reason.value,
                },
            )
            if internal_id is not None:
                self._ensure_match_mapping_available(
                    internal_id=internal_id,
                    external_id=fixture.external_id,
                )
                self._save_mapping("match", internal_id, fixture.external_id)

        if internal_id is None:
            internal_id = self.db.execute(
                text(
                    """
                    INSERT INTO matches (
                        competition_id, season_id, stage_id, round_id,
                        home_team_id, away_team_id,
                        kickoff_at, status, home_score, away_score,
                        provider_updated_at, last_synced_at,
                        created_at, updated_at
                    )
                    VALUES (
                        :competition_id, :season_id, :stage_id, :round_id,
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
                    "stage_id": stage_id,
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
                        stage_id = COALESCE(:stage_id, stage_id),
                        round_id = COALESCE(:round_id, round_id),
                        home_team_id = :home_team_id,
                        away_team_id = :away_team_id,
                        kickoff_at = :kickoff_at,
                        status = :status,
                        home_score = COALESCE(:home_score, home_score),
                        away_score = COALESCE(:away_score, away_score),
                        provider_updated_at = COALESCE(
                            :provider_updated_at,
                            provider_updated_at
                        ),
                        last_synced_at = :last_synced_at,
                        updated_at = :now
                    WHERE id = :internal_id
                    """
                ),
                {
                    "competition_id": competition_id,
                    "season_id": season_id,
                    "stage_id": stage_id,
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

    def _provider_team_canonical_names(
        self,
        item: ProviderTeam,
    ) -> tuple[str, ...]:
        labels = tuple(
            dict.fromkeys(
                label.strip()
                for label in (item.name, *item.aliases)
                if label and label.strip()
            )
        )
        return canonical_names_for_provider_team(
            provider=self.provider.name,
            external_id=item.external_id,
            labels=labels,
        )

    def _team_candidate_ids(self, name: str) -> set[int]:
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

        return candidates

    def _reconcile_provider_team(
        self,
        item: ProviderTeam,
        canonical_names: tuple[str, ...] | None = None,
    ) -> int | None:
        canonical_names = (
            canonical_names
            if canonical_names is not None
            else self._provider_team_canonical_names(item)
        )
        evidence = tuple(
            dict.fromkeys(
                label.strip()
                for label in (item.name, *item.aliases, *canonical_names)
                if label and label.strip()
            )
        )
        candidates: set[int] = set()
        for label in evidence:
            candidates.update(self._team_candidate_ids(label))

        if len(candidates) > 1:
            raise ValueError(
                "Ambiguous team reconciliation for provider team "
                f"'{item.name}' ({item.external_id}); "
                f"evidence={list(evidence)}, candidates={sorted(candidates)}"
            )
        return next(iter(candidates)) if candidates else None

    def _reconcile_team(self, name: str) -> int | None:
        candidates = self._team_candidate_ids(name)
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
