import csv
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(slots=True)
class BrasileiraoCsvImportResult:
    rows_seen: int = 0
    matches_created: int = 0
    matches_updated: int = 0
    statistics_upserted: int = 0
    teams_created: int = 0
    teams_reused: int = 0


class BrasileiraoCsvImporter:
    def __init__(self, db: Session) -> None:
        self.db = db

    def import_file(self, file_path: str | Path) -> BrasileiraoCsvImportResult:
        path = Path(file_path)
        result = BrasileiraoCsvImportResult()
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if not rows:
            return result

        competition_name = (rows[0].get("competition") or "Brasileirão Série A").strip()
        season_name = str(rows[0].get("season") or "").strip()
        if not season_name:
            raise ValueError("CSV season is required")

        competition_id = self._ensure_competition(competition_name)
        season_id = self._ensure_season(competition_id, season_name)

        for row in rows:
            result.rows_seen += 1
            round_number = self._to_int(row.get("round"))
            match_date = self._to_date(row.get("date"))
            home_name = self._required(row.get("home_team"), "home_team")
            away_name = self._required(row.get("away_team"), "away_team")

            home_id, created = self._ensure_team(home_name)
            result.teams_created += int(created)
            result.teams_reused += int(not created)
            away_id, created = self._ensure_team(away_name)
            result.teams_created += int(created)
            result.teams_reused += int(not created)

            round_id = None
            if round_number is not None:
                round_id = self._ensure_round(season_id, round_number)

            match_id, created = self._upsert_match(
                competition_id=competition_id,
                season_id=season_id,
                round_id=round_id,
                home_team_id=home_id,
                away_team_id=away_id,
                kickoff_at=self._kickoff(match_date, row.get("kickoff_time")),
                match_date=match_date,
                home_score=self._to_int(row.get("home_score")),
                away_score=self._to_int(row.get("away_score")),
                ht_home_score=self._to_int(row.get("ht_home_score")),
                ht_away_score=self._to_int(row.get("ht_away_score")),
            )
            result.matches_created += int(created)
            result.matches_updated += int(not created)

            provenance = {
                "source_type": "chatgpt_research_csv",
                "source_name": self._clean(row.get("source_name")),
                "source_url": self._clean(row.get("source_url")),
                "secondary_source_url": self._clean(row.get("secondary_source_url")),
                "confidence": self._clean(row.get("confidence")),
                "collected_at": self._clean(row.get("collected_at")),
            }
            for team_id, prefix in ((home_id, "home"), (away_id, "away")):
                values = self._statistics(row, prefix)
                if any(value is not None for value in values.values()):
                    self._upsert_statistics(match_id, team_id, values, provenance)
                    result.statistics_upserted += 1

        self.db.commit()
        return result

    def _ensure_competition(self, name: str) -> int:
        existing = self.db.execute(
            text("SELECT id FROM competitions WHERE name = :name ORDER BY id LIMIT 1"),
            {"name": name},
        ).scalar_one_or_none()
        if existing:
            return existing
        now = datetime.now(UTC)
        return self.db.execute(
            text(
                """
                INSERT INTO competitions (
                    name, short_name, country_code, competition_type,
                    is_active, created_at, updated_at
                ) VALUES (
                    :name, 'Brasileirão', 'BRA', 'league', true, :now, :now
                ) RETURNING id
                """
            ),
            {"name": name, "now": now},
        ).scalar_one()

    def _ensure_season(self, competition_id: int, name: str) -> int:
        existing = self.db.execute(
            text(
                """
                SELECT id FROM seasons
                WHERE competition_id = :competition_id AND name = :name
                ORDER BY id LIMIT 1
                """
            ),
            {"competition_id": competition_id, "name": name},
        ).scalar_one_or_none()
        if existing:
            return existing
        now = datetime.now(UTC)
        year = int(name) if name.isdigit() else None
        return self.db.execute(
            text(
                """
                INSERT INTO seasons (
                    competition_id, name, start_date, end_date, is_current,
                    year_start, year_end, created_at, updated_at
                ) VALUES (
                    :competition_id, :name, :start_date, :end_date, false,
                    :year_start, :year_end, :now, :now
                ) RETURNING id
                """
            ),
            {
                "competition_id": competition_id,
                "name": name,
                "start_date": f"{year}-01-01" if year else None,
                "end_date": f"{year}-12-31" if year else None,
                "year_start": year,
                "year_end": year,
                "now": now,
            },
        ).scalar_one()

    def _ensure_round(self, season_id: int, round_number: int) -> int:
        existing = self.db.execute(
            text(
                """
                SELECT id FROM rounds
                WHERE season_id = :season_id AND round_number = :round_number
                ORDER BY id LIMIT 1
                """
            ),
            {"season_id": season_id, "round_number": round_number},
        ).scalar_one_or_none()
        if existing:
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

    def _ensure_team(self, name: str) -> tuple[int, bool]:
        target = self._normalize(name)
        candidates: dict[int, str] = {}
        for row in self.db.execute(text("SELECT id, name FROM teams")).all():
            if self._normalize(row.name) == target:
                candidates[row.id] = row.name
        for row in self.db.execute(
            text("SELECT team_id, alias FROM team_aliases")
        ).all():
            if self._normalize(row.alias) == target:
                candidates[row.team_id] = row.alias

        if len(candidates) == 1:
            return next(iter(candidates)), False
        if len(candidates) > 1:
            raise ValueError(f"Ambiguous team reconciliation for '{name}': {candidates}")

        now = datetime.now(UTC)
        team_id = self.db.execute(
            text(
                """
                INSERT INTO teams (
                    name, short_name, country_code, created_at, updated_at
                ) VALUES (:name, NULL, 'BRA', :now, :now)
                RETURNING id
                """
            ),
            {"name": name, "now": now},
        ).scalar_one()
        self.db.execute(
            text(
                """
                INSERT INTO team_aliases (team_id, alias, normalized_alias)
                VALUES (:team_id, :alias, :normalized_alias)
                ON CONFLICT (normalized_alias) DO NOTHING
                """
            ),
            {"team_id": team_id, "alias": name, "normalized_alias": target},
        )
        return team_id, True

    def _upsert_match(
        self,
        *,
        competition_id: int,
        season_id: int,
        round_id: int | None,
        home_team_id: int,
        away_team_id: int,
        kickoff_at: datetime,
        match_date: date,
        home_score: int | None,
        away_score: int | None,
        ht_home_score: int | None,
        ht_away_score: int | None,
    ) -> tuple[int, bool]:
        existing = self.db.execute(
            text(
                """
                SELECT id FROM matches
                WHERE season_id = :season_id
                  AND home_team_id = :home_team_id
                  AND away_team_id = :away_team_id
                  AND (kickoff_at AT TIME ZONE 'America/Sao_Paulo')::date = :match_date
                ORDER BY id LIMIT 1
                """
            ),
            {
                "season_id": season_id,
                "home_team_id": home_team_id,
                "away_team_id": away_team_id,
                "match_date": match_date,
            },
        ).scalar_one_or_none()
        now = datetime.now(UTC)
        winner_team_id = self._winner(
            home_team_id, away_team_id, home_score, away_score
        )
        values = {
            "competition_id": competition_id,
            "round_id": round_id,
            "kickoff_at": kickoff_at,
            "home_score": home_score,
            "away_score": away_score,
            "ht_home_score": ht_home_score,
            "ht_away_score": ht_away_score,
            "winner_team_id": winner_team_id,
            "now": now,
        }
        if existing:
            self.db.execute(
                text(
                    """
                    UPDATE matches
                    SET competition_id = :competition_id,
                        round_id = :round_id,
                        kickoff_at = :kickoff_at,
                        status = 'finished',
                        home_score = :home_score,
                        away_score = :away_score,
                        home_score_ht = COALESCE(:ht_home_score, home_score_ht),
                        away_score_ht = COALESCE(:ht_away_score, away_score_ht),
                        winner_team_id = :winner_team_id,
                        updated_at = :now
                    WHERE id = :id
                    """
                ),
                {**values, "id": existing},
            )
            return existing, False

        match_id = self.db.execute(
            text(
                """
                INSERT INTO matches (
                    competition_id, season_id, round_id,
                    home_team_id, away_team_id, kickoff_at, status,
                    home_score, away_score, home_score_ht, away_score_ht,
                    winner_team_id, created_at, updated_at
                ) VALUES (
                    :competition_id, :season_id, :round_id,
                    :home_team_id, :away_team_id, :kickoff_at, 'finished',
                    :home_score, :away_score, :ht_home_score, :ht_away_score,
                    :winner_team_id, :now, :now
                ) RETURNING id
                """
            ),
            {
                **values,
                "season_id": season_id,
                "home_team_id": home_team_id,
                "away_team_id": away_team_id,
            },
        ).scalar_one()
        return match_id, True

    def _upsert_statistics(
        self,
        match_id: int,
        team_id: int,
        values: dict,
        provenance: dict,
    ) -> None:
        self.db.execute(
            text(
                """
                INSERT INTO match_team_statistics (
                    match_id, team_id, period, shots, shots_on_target, xg,
                    corners, yellow_cards, red_cards, possession, extra
                ) VALUES (
                    :match_id, :team_id, 'FULL_TIME', :shots, :shots_on_target,
                    :xg, :corners, :yellow_cards, :red_cards, :possession,
                    CAST(:extra AS jsonb)
                )
                ON CONFLICT (match_id, team_id, period)
                DO UPDATE SET
                    shots = COALESCE(EXCLUDED.shots, match_team_statistics.shots),
                    shots_on_target = COALESCE(
                        EXCLUDED.shots_on_target,
                        match_team_statistics.shots_on_target
                    ),
                    xg = COALESCE(EXCLUDED.xg, match_team_statistics.xg),
                    corners = COALESCE(EXCLUDED.corners, match_team_statistics.corners),
                    yellow_cards = COALESCE(
                        EXCLUDED.yellow_cards,
                        match_team_statistics.yellow_cards
                    ),
                    red_cards = COALESCE(
                        EXCLUDED.red_cards,
                        match_team_statistics.red_cards
                    ),
                    possession = COALESCE(
                        EXCLUDED.possession,
                        match_team_statistics.possession
                    ),
                    extra = match_team_statistics.extra || EXCLUDED.extra
                """
            ),
            {
                "match_id": match_id,
                "team_id": team_id,
                "extra": json.dumps(provenance),
                **values,
            },
        )

    def _statistics(self, row: dict[str, str], prefix: str) -> dict:
        return {
            "shots": self._to_int(row.get(f"{prefix}_shots")),
            "shots_on_target": self._to_int(row.get(f"{prefix}_shots_on_target")),
            "xg": self._to_decimal(row.get(f"{prefix}_xg")),
            "corners": self._to_int(row.get(f"{prefix}_corners")),
            "yellow_cards": self._to_int(row.get(f"{prefix}_yellow_cards")),
            "red_cards": self._to_int(row.get(f"{prefix}_red_cards")),
            "possession": self._to_decimal(row.get(f"{prefix}_possession")),
        }

    @staticmethod
    def _winner(
        home_id: int,
        away_id: int,
        home_score: int | None,
        away_score: int | None,
    ) -> int | None:
        if home_score is None or away_score is None or home_score == away_score:
            return None
        return home_id if home_score > away_score else away_id

    @staticmethod
    def _required(value: str | None, field: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError(f"CSV field '{field}' is required")
        return cleaned

    @staticmethod
    def _clean(value: str | None) -> str | None:
        cleaned = (value or "").strip()
        return cleaned or None

    @staticmethod
    def _to_int(value: str | None) -> int | None:
        cleaned = (value or "").strip()
        if not cleaned:
            return None
        return int(Decimal(cleaned.replace(",", ".")))

    @staticmethod
    def _to_decimal(value: str | None) -> Decimal | None:
        cleaned = (value or "").strip()
        if not cleaned:
            return None
        return Decimal(cleaned.replace(",", "."))

    @staticmethod
    def _to_date(value: str | None) -> date:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("CSV field 'date' is required")
        return datetime.strptime(cleaned, "%Y-%m-%d").date()

    @staticmethod
    def _kickoff(match_date: date, kickoff_time: str | None) -> datetime:
        time_value = (kickoff_time or "12:00").strip() or "12:00"
        local = datetime.strptime(
            f"{match_date.isoformat()} {time_value}", "%Y-%m-%d %H:%M"
        )
        return local.replace(tzinfo=ZoneInfo("America/Sao_Paulo")).astimezone(UTC)

    @staticmethod
    def _normalize(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value.lower())
        normalized = "".join(
            char for char in normalized if not unicodedata.combining(char)
        )
        return re.sub(r"[^a-z0-9]+", "", normalized)
