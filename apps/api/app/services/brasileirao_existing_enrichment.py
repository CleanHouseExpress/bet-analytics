import csv
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(slots=True)
class ExistingEnrichmentResult:
    rows_seen: int = 0
    matches_enriched: int = 0
    rows_skipped_missing_match: int = 0
    rows_skipped_ambiguous_match: int = 0
    statistics_upserted: int = 0
    ht_updates: int = 0
    goal_events_added: int = 0
    goal_events_reused: int = 0
    teams_resolved: int = 0


class BrasileiraoExistingEnrichmentImporter:
    SOURCE_TYPE = "chatgpt_research_csv"

    def __init__(self, db: Session) -> None:
        self.db = db

    def import_file(self, file_path: str | Path) -> ExistingEnrichmentResult:
        path = Path(file_path)
        result = ExistingEnrichmentResult()
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if not rows:
            return result

        competition_name = (rows[0].get("competition") or "Brasileirão Série A").strip()
        season_name = str(rows[0].get("season") or "").strip()
        if not season_name:
            raise ValueError("CSV season is required")

        competition_id = self._find_competition(competition_name)
        season_id = self._find_season(competition_id, season_name)

        try:
            for row in rows:
                result.rows_seen += 1
                match_date = self._to_date(row.get("date"))
                home_name = self._required(row.get("home_team"), "home_team")
                away_name = self._required(row.get("away_team"), "away_team")
                home_id = self._resolve_team(home_name)
                away_id = self._resolve_team(away_name)
                result.teams_resolved += 2

                matches = self._find_matches(
                    season_id=season_id,
                    home_team_id=home_id,
                    away_team_id=away_id,
                    match_date=match_date,
                    home_score=self._to_int(row.get("home_score")),
                    away_score=self._to_int(row.get("away_score")),
                )
                if not matches:
                    result.rows_skipped_missing_match += 1
                    continue
                if len(matches) > 1:
                    result.rows_skipped_ambiguous_match += 1
                    continue

                match_id = matches[0]
                result.matches_enriched += 1
                if self._update_ht(match_id, row):
                    result.ht_updates += 1

                provenance = {
                    "source_type": self.SOURCE_TYPE,
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

                added, reused = self._reconcile_goals(
                    match_id=match_id,
                    home_team_id=home_id,
                    away_team_id=away_id,
                    home_goals=row.get("home_goals_minutes"),
                    away_goals=row.get("away_goals_minutes"),
                    provenance=provenance,
                )
                result.goal_events_added += added
                result.goal_events_reused += reused

            self.db.commit()
            return result
        except Exception:
            self.db.rollback()
            raise

    def _find_competition(self, name: str) -> int:
        rows = self.db.execute(
            text("SELECT id FROM competitions WHERE name = :name ORDER BY id"),
            {"name": name},
        ).scalars().all()
        if len(rows) != 1:
            raise ValueError(
                f"Existing-only mode requires exactly one competition '{name}', found {len(rows)}"
            )
        return rows[0]

    def _find_season(self, competition_id: int, name: str) -> int:
        rows = self.db.execute(
            text(
                """
                SELECT id FROM seasons
                WHERE competition_id = :competition_id AND name = :name
                ORDER BY id
                """
            ),
            {"competition_id": competition_id, "name": name},
        ).scalars().all()
        if len(rows) != 1:
            raise ValueError(
                f"Existing-only mode requires exactly one season '{name}', found {len(rows)}"
            )
        return rows[0]

    def _resolve_team(self, name: str) -> int:
        target = self._normalize(name)
        candidates: set[int] = set()
        for row in self.db.execute(text("SELECT id, name FROM teams")).all():
            if self._normalize(row.name) == target:
                candidates.add(row.id)
        for row in self.db.execute(text("SELECT team_id, alias FROM team_aliases")).all():
            if self._normalize(row.alias) == target:
                candidates.add(row.team_id)
        if len(candidates) == 1:
            return next(iter(candidates))
        if not candidates:
            raise ValueError(f"Team '{name}' does not exist; existing-only mode never creates teams")
        raise ValueError(f"Ambiguous team reconciliation for '{name}': {sorted(candidates)}")

    def _find_matches(
        self,
        *,
        season_id: int,
        home_team_id: int,
        away_team_id: int,
        match_date: date,
        home_score: int | None,
        away_score: int | None,
    ) -> list[int]:
        return self.db.execute(
            text(
                """
                SELECT id
                FROM matches
                WHERE season_id = :season_id
                  AND home_team_id = :home_team_id
                  AND away_team_id = :away_team_id
                  AND (kickoff_at AT TIME ZONE 'America/Sao_Paulo')::date = :match_date
                  AND (:home_score IS NULL OR home_score = :home_score)
                  AND (:away_score IS NULL OR away_score = :away_score)
                ORDER BY id
                """
            ),
            {
                "season_id": season_id,
                "home_team_id": home_team_id,
                "away_team_id": away_team_id,
                "match_date": match_date,
                "home_score": home_score,
                "away_score": away_score,
            },
        ).scalars().all()

    def _update_ht(self, match_id: int, row: dict[str, str]) -> bool:
        home_ht = self._to_int(row.get("ht_home_score"))
        away_ht = self._to_int(row.get("ht_away_score"))
        if home_ht is None and away_ht is None:
            return False
        self.db.execute(
            text(
                """
                UPDATE matches
                SET home_score_ht = COALESCE(home_score_ht, :home_ht),
                    away_score_ht = COALESCE(away_score_ht, :away_ht),
                    updated_at = now()
                WHERE id = :match_id
                """
            ),
            {"match_id": match_id, "home_ht": home_ht, "away_ht": away_ht},
        )
        return True

    def _reconcile_goals(
        self,
        *,
        match_id: int,
        home_team_id: int,
        away_team_id: int,
        home_goals: str | None,
        away_goals: str | None,
        provenance: dict,
    ) -> tuple[int, int]:
        desired = []
        desired.extend(self._parse_goal_list(home_goals, home_team_id))
        desired.extend(self._parse_goal_list(away_goals, away_team_id))
        if not desired:
            return 0, 0

        existing_rows = self.db.execute(
            text(
                """
                SELECT team_id, minute, extra_minute, event_subtype
                FROM match_events
                WHERE match_id = :match_id AND event_type = 'goal'
                """
            ),
            {"match_id": match_id},
        ).all()
        existing = Counter(
            (
                row.team_id,
                row.minute,
                row.extra_minute,
                self._normalize_subtype(row.event_subtype),
            )
            for row in existing_rows
        )

        added = 0
        reused = 0
        consumed = Counter()
        for team_id, minute, extra, subtype, raw_token in desired:
            key = (team_id, minute, extra, subtype)
            if consumed[key] < existing[key]:
                consumed[key] += 1
                reused += 1
                continue

            period = "FIRST_HALF" if minute <= 45 else "SECOND_HALF"
            metadata = {
                **provenance,
                "source": "brasileirao_enriched_csv",
                "raw_goal_token": raw_token,
            }
            self.db.execute(
                text(
                    """
                    INSERT INTO match_events (
                        match_id, team_id, period, minute, extra_minute,
                        event_type, event_subtype, value, metadata, created_at
                    ) VALUES (
                        :match_id, :team_id, :period, :minute, :extra_minute,
                        'goal', :event_subtype, :value, CAST(:metadata AS jsonb), now()
                    )
                    """
                ),
                {
                    "match_id": match_id,
                    "team_id": team_id,
                    "period": period,
                    "minute": minute,
                    "extra_minute": extra,
                    "event_subtype": subtype,
                    "value": raw_token,
                    "metadata": json.dumps(metadata, ensure_ascii=False),
                },
            )
            consumed[key] += 1
            added += 1
        return added, reused

    def _parse_goal_list(
        self,
        raw: str | None,
        team_id: int,
    ) -> list[tuple[int, int, int | None, str | None, str]]:
        cleaned = self._clean(raw)
        if not cleaned:
            return []
        parsed = []
        for token in cleaned.split("|"):
            token = token.strip()
            match = re.fullmatch(r"(\d+)(?:\+(\d+))?(?:\((PEN|OG)\))?", token.upper())
            if not match:
                continue
            minute = int(match.group(1))
            extra = int(match.group(2)) if match.group(2) else None
            marker = match.group(3)
            subtype = "penalty" if marker == "PEN" else "own_goal" if marker == "OG" else None
            parsed.append((team_id, minute, extra, subtype, token))
        return parsed

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
                    shots = COALESCE(match_team_statistics.shots, EXCLUDED.shots),
                    shots_on_target = COALESCE(
                        match_team_statistics.shots_on_target,
                        EXCLUDED.shots_on_target
                    ),
                    xg = COALESCE(match_team_statistics.xg, EXCLUDED.xg),
                    corners = COALESCE(match_team_statistics.corners, EXCLUDED.corners),
                    yellow_cards = COALESCE(
                        match_team_statistics.yellow_cards,
                        EXCLUDED.yellow_cards
                    ),
                    red_cards = COALESCE(
                        match_team_statistics.red_cards,
                        EXCLUDED.red_cards
                    ),
                    possession = COALESCE(
                        match_team_statistics.possession,
                        EXCLUDED.possession
                    ),
                    extra = match_team_statistics.extra || EXCLUDED.extra
                """
            ),
            {
                "match_id": match_id,
                "team_id": team_id,
                "extra": json.dumps(provenance, ensure_ascii=False),
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
    def _normalize_subtype(value: str | None) -> str | None:
        if not value:
            return None
        normalized = value.lower().strip()
        aliases = {
            "pen": "penalty",
            "penalty_goal": "penalty",
            "own-goal": "own_goal",
            "owngoal": "own_goal",
        }
        return aliases.get(normalized, normalized)

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
    def _normalize(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value.lower())
        normalized = "".join(
            char for char in normalized if not unicodedata.combining(char)
        )
        return re.sub(r"[^a-z0-9]+", "", normalized)
