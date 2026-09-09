import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.orm import Session

from apps.api.app.providers.base import FootballDataProvider
from apps.api.app.providers.contracts import ProviderCompetition, ProviderSeason, ProviderTeam


SERIES = {
    "A": "Brasileirão Série A",
    "B": "Brasileirão Série B",
    "C": "Brasileirão Série C",
    "D": "Brasileirão Série D",
}


@dataclass(slots=True)
class LeagueCatalogResult:
    series: str
    provider_competition_id: str
    provider_season_id: str
    competition_id: int
    season_id: int
    teams_seen: int = 0
    teams_created: int = 0
    teams_mapped: int = 0
    teams_reused: int = 0
    ambiguous: list[dict[str, str]] = field(default_factory=list)


class BrazilianLeaguesCatalogSync:
    def __init__(self, db: Session, provider: FootballDataProvider) -> None:
        self.db = db
        self.provider = provider

    async def sync(self, year: int, series: Iterable[str] = ("A", "B", "C", "D")) -> list[LeagueCatalogResult]:
        competitions = await self.provider.get_competitions()
        results: list[LeagueCatalogResult] = []

        for level in series:
            level = level.upper()
            if level not in SERIES:
                raise ValueError(f"Unsupported Brazilian league series: {level}")

            provider_competition = self._find_series_competition(competitions, level)
            provider_seasons = await self.provider.get_seasons(provider_competition.external_id)
            provider_season = self._find_year_season(provider_seasons, year)

            competition_id = self._ensure_competition(level, provider_competition)
            season_id = self._ensure_season(competition_id, year, provider_season)
            self._save_mapping("competition", competition_id, provider_competition.external_id)
            self._save_mapping("season", season_id, provider_season.external_id)

            result = LeagueCatalogResult(
                series=level,
                provider_competition_id=provider_competition.external_id,
                provider_season_id=provider_season.external_id,
                competition_id=competition_id,
                season_id=season_id,
            )

            teams = await self.provider.get_teams(provider_season.external_id)
            for team in teams:
                result.teams_seen += 1
                existing_mapping = self._mapping("team", team.external_id)
                if existing_mapping is not None:
                    self._update_team(existing_mapping, team)
                    result.teams_mapped += 1
                    result.teams_reused += 1
                    continue

                team_id, match_kind, candidates = self._resolve_local_team(team.name)
                if match_kind == "ambiguous":
                    result.ambiguous.append(
                        {
                            "provider_team": team.name,
                            "external_id": team.external_id,
                            "candidates": ", ".join(candidates),
                        }
                    )
                    continue

                if team_id is None:
                    team_id = self._create_team(team)
                    result.teams_created += 1
                else:
                    self._update_team(team_id, team)
                    result.teams_reused += 1

                self._save_mapping("team", team_id, team.external_id)
                self._ensure_alias(team_id, team.name)
                result.teams_mapped += 1

            self.db.commit()
            results.append(result)

        return results

    def _find_series_competition(
        self, competitions: list[ProviderCompetition], level: str
    ) -> ProviderCompetition:
        expected = {
            "A": ("serie a", "série a", "brasileirao serie a", "brasileirão série a"),
            "B": ("serie b", "série b", "brasileirao serie b", "brasileirão série b"),
            "C": ("serie c", "série c", "brasileirao serie c", "brasileirão série c"),
            "D": ("serie d", "série d", "brasileirao serie d", "brasileirão série d"),
        }[level]
        matches: list[ProviderCompetition] = []
        for item in competitions:
            country = (item.country_code or "").upper()
            name = self._normalize(item.name)
            if country not in {"BR", "BRA", ""}:
                continue
            if any(self._normalize(term) == name or self._normalize(term) in name for term in expected):
                matches.append(item)
        if len(matches) != 1:
            found = [f"{item.external_id}:{item.name}" for item in matches]
            raise ValueError(f"Could not uniquely resolve Brazilian Série {level}: {found}")
        return matches[0]

    @staticmethod
    def _find_year_season(seasons: list[ProviderSeason], year: int) -> ProviderSeason:
        exact = [item for item in seasons if str(year) == str(item.name).strip()]
        if len(exact) == 1:
            return exact[0]
        dated = [
            item
            for item in seasons
            if item.start_date is not None and item.start_date.year == year
        ]
        if len(dated) == 1:
            return dated[0]
        found = [f"{item.external_id}:{item.name}" for item in seasons]
        raise ValueError(f"Could not uniquely resolve season {year}: {found}")

    def _ensure_competition(self, level: str, item: ProviderCompetition) -> int:
        mapped = self._mapping("competition", item.external_id)
        if mapped is not None:
            return mapped
        canonical = SERIES[level]
        existing = self.db.execute(
            text("SELECT id FROM competitions WHERE lower(name) = lower(:name) ORDER BY id LIMIT 1"),
            {"name": canonical},
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        return self.db.execute(
            text(
                """
                INSERT INTO competitions (
                    name, short_name, country_code, competition_type, is_active,
                    created_at, updated_at
                ) VALUES (:name, :short_name, 'BRA', 'league', true, now(), now())
                RETURNING id
                """
            ),
            {"name": canonical, "short_name": f"Série {level}"},
        ).scalar_one()

    def _ensure_season(self, competition_id: int, year: int, item: ProviderSeason) -> int:
        mapped = self._mapping("season", item.external_id)
        if mapped is not None:
            return mapped
        existing = self.db.execute(
            text(
                """
                SELECT id FROM seasons
                WHERE competition_id = :competition_id AND name = :name
                ORDER BY id LIMIT 1
                """
            ),
            {"competition_id": competition_id, "name": str(year)},
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        return self.db.execute(
            text(
                """
                INSERT INTO seasons (
                    competition_id, name, start_date, end_date, is_current,
                    year_start, year_end, created_at, updated_at
                ) VALUES (
                    :competition_id, :name, :start_date, :end_date, true,
                    :year, :year, now(), now()
                ) RETURNING id
                """
            ),
            {
                "competition_id": competition_id,
                "name": str(year),
                "start_date": item.start_date.date() if item.start_date else None,
                "end_date": item.end_date.date() if item.end_date else None,
                "year": year,
            },
        ).scalar_one()

    def _resolve_local_team(self, provider_name: str) -> tuple[int | None, str, list[str]]:
        normalized = self._normalize(provider_name)
        rows = self.db.execute(
            text(
                """
                SELECT t.id, t.name, a.normalized_alias
                FROM teams t
                LEFT JOIN team_aliases a ON a.team_id = t.id
                WHERE t.country_code IN ('BRA', 'BR') OR t.country_code IS NULL
                """
            )
        ).all()

        exact_ids: dict[int, str] = {}
        scored: list[tuple[float, int, str]] = []
        for row in rows:
            values = {self._normalize(row.name)}
            if row.normalized_alias:
                values.add(self._normalize(row.normalized_alias))
            if normalized in values:
                exact_ids[row.id] = row.name
                continue
            score = max(SequenceMatcher(None, normalized, value).ratio() for value in values)
            scored.append((score, row.id, row.name))

        if len(exact_ids) == 1:
            team_id, team_name = next(iter(exact_ids.items()))
            return team_id, "exact", [team_name]
        if len(exact_ids) > 1:
            return None, "ambiguous", list(exact_ids.values())

        scored.sort(reverse=True)
        strong = [item for item in scored if item[0] >= 0.94]
        if len(strong) == 1:
            return strong[0][1], "fuzzy", [strong[0][2]]

        review = [item for item in scored if item[0] >= 0.82][:3]
        if review:
            return None, "ambiguous", [f"{name} ({score:.2f})" for score, _, name in review]
        return None, "new", []

    def _create_team(self, item: ProviderTeam) -> int:
        return self.db.execute(
            text(
                """
                INSERT INTO teams (
                    name, short_name, country_code, is_active, created_at, updated_at
                ) VALUES (:name, NULL, COALESCE(:country_code, 'BRA'), true, now(), now())
                RETURNING id
                """
            ),
            {"name": item.name, "country_code": item.country_code},
        ).scalar_one()

    def _update_team(self, team_id: int, item: ProviderTeam) -> None:
        self.db.execute(
            text(
                """
                UPDATE teams
                SET country_code = COALESCE(:country_code, country_code, 'BRA'),
                    is_active = true,
                    updated_at = now()
                WHERE id = :team_id
                """
            ),
            {"team_id": team_id, "country_code": item.country_code},
        )
        self._ensure_alias(team_id, item.name)

    def _ensure_alias(self, team_id: int, alias: str) -> None:
        normalized = self._normalize(alias)
        existing = self.db.execute(
            text("SELECT team_id FROM team_aliases WHERE normalized_alias = :alias"),
            {"alias": normalized},
        ).scalar_one_or_none()
        if existing is None:
            self.db.execute(
                text(
                    """
                    INSERT INTO team_aliases (team_id, alias, normalized_alias)
                    VALUES (:team_id, :alias, :normalized_alias)
                    """
                ),
                {"team_id": team_id, "alias": alias, "normalized_alias": normalized},
            )

    def _mapping(self, entity_type: str, external_id: str) -> int | None:
        return self.db.execute(
            text(
                """
                SELECT internal_id FROM external_entity_mappings
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
                ) VALUES (:entity_type, :internal_id, :provider, :external_id)
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

    @staticmethod
    def _normalize(value: str) -> str:
        value = unicodedata.normalize("NFKD", value or "")
        value = "".join(char for char in value if not unicodedata.combining(char))
        value = value.lower().replace("&", " e ")
        return re.sub(r"[^a-z0-9]+", " ", value).strip()
