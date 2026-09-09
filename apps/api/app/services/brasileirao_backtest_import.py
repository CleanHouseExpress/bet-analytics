import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(slots=True)
class BacktestImportResult:
    matches: int = 0
    matches_updated: int = 0
    goal_events: int = 0
    pregame_odds: int = 0
    live_odds: int = 0
    checkpoints: int = 0
    strategies: int = 0
    skipped: int = 0


class BrasileiraoBacktestImporter:
    """Import the historical Brasileirão backtest workbook into the canonical football schema.

    Source priority:
    1. Rodadas Detalhadas: authoritative dated fixture/result/goal timeline source.
    2. Temporada 2026: complements HT scores and 1X2 odds where a dated fixture exists.
    3. Jogos: complements 1X2 odds and provides the workbook IDs used by Checkpoints.
    4. Checkpoints: imports only populated live snapshots; placeholders are ignored.
    5. Teses: strategy catalogue.
    """

    SOURCE = "backtest_brasileirao_estrategias_teses_v1"
    COMPETITION_NAME = "Brasileirão Série A"
    SEASON_NAME = "2026"

    TEAM_CODES = {
        "CAM": "Atlético-MG",
        "CAP": "Athletico-PR",
        "BAH": "Bahia",
        "BOT": "Botafogo",
        "RBB": "Bragantino",
        "CHA": "Chapecoense",
        "COR": "Corinthians",
        "CFC": "Coritiba",
        "CRU": "Cruzeiro",
        "FLA": "Flamengo",
        "FLU": "Fluminense",
        "GRE": "Grêmio",
        "INT": "Internacional",
        "MIR": "Mirassol",
        "PAL": "Palmeiras",
        "REM": "Remo",
        "SAN": "Santos",
        "SAO": "São Paulo",
        "VAS": "Vasco",
        "VIT": "Vitória",
    }

    def __init__(self, db: Session) -> None:
        self.db = db
        self.result = BacktestImportResult()
        self._workbook_match_ids: dict[int, int] = {}

    def import_file(self, file_name: str | Path) -> BacktestImportResult:
        path = Path(file_name)
        workbook = load_workbook(path, data_only=True, read_only=True)
        try:
            competition_id = self._ensure_competition()
            season_id = self._ensure_season(competition_id)
            self._import_detailed_rounds(workbook, competition_id, season_id)
            self._import_season_sheet(workbook, competition_id, season_id)
            self._import_games_sheet(workbook, competition_id, season_id)
            self._import_checkpoints(workbook)
            self._import_strategies(workbook)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        finally:
            workbook.close()
        return self.result

    def _import_detailed_rounds(self, workbook, competition_id: int, season_id: int) -> None:
        if "Rodadas Detalhadas" not in workbook.sheetnames:
            return
        for row in self._rows(workbook["Rodadas Detalhadas"]):
            match_date = self._as_date(row.get("Data"))
            home = self._clean(row.get("Mandante"))
            away = self._clean(row.get("Visitante"))
            if not match_date or not home or not away:
                self.result.skipped += 1
                continue
            home_score, away_score = self._parse_score(row.get("Resultado"))
            round_number = self._as_int(row.get("Rodada"))
            match_id = self._upsert_match(
                competition_id,
                season_id,
                match_date,
                home,
                away,
                home_score,
                away_score,
                round_number,
            )
            self._replace_goal_events(match_id, home, away, row.get("Minutos dos gols"))
            favorite = self._clean(row.get("Favorito pré-jogo"))
            favorite_odd = self._as_decimal(row.get("Odd pré favorito"))
            if favorite and favorite_odd:
                bookmaker = self._bookmaker_from_source(row.get("Fonte odds"))
                self._upsert_single_pregame_odd(match_id, bookmaker, favorite, favorite_odd)

    def _import_season_sheet(self, workbook, competition_id: int, season_id: int) -> None:
        if "Temporada 2026" not in workbook.sheetnames:
            return
        for row in self._rows(workbook["Temporada 2026"]):
            match_date = self._as_date(row.get("Data"))
            home = self._clean(row.get("Mandante"))
            away = self._clean(row.get("Visitante"))
            if not match_date or not home or not away:
                continue
            home_score = self._as_int(row.get("FT Mandante"))
            away_score = self._as_int(row.get("FT Visitante"))
            match_id = self._upsert_match(
                competition_id,
                season_id,
                match_date,
                home,
                away,
                home_score,
                away_score,
                None,
            )
            ht_home = self._as_int(row.get("HT Mandante"))
            ht_away = self._as_int(row.get("HT Visitante"))
            if ht_home is not None or ht_away is not None:
                self.db.execute(
                    text(
                        """
                        UPDATE matches
                        SET home_score_ht = COALESCE(:home_ht, home_score_ht),
                            away_score_ht = COALESCE(:away_ht, away_score_ht),
                            updated_at = now()
                        WHERE id = :match_id
                        """
                    ),
                    {"match_id": match_id, "home_ht": ht_home, "away_ht": ht_away},
                )
            self._upsert_1x2_odds(
                match_id,
                self._bookmaker_from_source(row.get("Fonte Odds")),
                self._as_decimal(row.get("Odd Mandante Pré")),
                self._as_decimal(row.get("Odd Empate Pré")),
                self._as_decimal(row.get("Odd Visitante Pré")),
            )

    def _import_games_sheet(self, workbook, competition_id: int, season_id: int) -> None:
        if "Jogos" not in workbook.sheetnames:
            return
        for row in self._rows(workbook["Jogos"]):
            workbook_id = self._as_int(row.get("ID"))
            home = self._clean(row.get("Mandante"))
            away = self._clean(row.get("Visitante"))
            if not workbook_id or not home or not away:
                continue
            home_score = self._as_int(row.get("Gols Mandante"))
            away_score = self._as_int(row.get("Gols Visitante"))
            match_date = self._as_date(row.get("Data"))
            match_id = None
            if match_date:
                match_id = self._upsert_match(
                    competition_id,
                    season_id,
                    match_date,
                    home,
                    away,
                    home_score,
                    away_score,
                    self._as_int(row.get("Rodada")),
                )
            else:
                match_id = self._find_match_by_pair(season_id, home, away)
            if match_id is None:
                self.result.skipped += 1
                continue
            self._workbook_match_ids[workbook_id] = match_id
            self._upsert_1x2_odds(
                match_id,
                self._bookmaker_from_source(row.get("Fonte Odds")),
                self._as_decimal(row.get("Odd Mandante Pré")),
                self._as_decimal(row.get("Odd Empate Pré")),
                self._as_decimal(row.get("Odd Visitante Pré")),
            )

    def _import_checkpoints(self, workbook) -> None:
        if "Checkpoints" not in workbook.sheetnames:
            return
        for row in self._rows(workbook["Checkpoints"]):
            workbook_id = self._as_int(row.get("Jogo ID"))
            minute = self._as_int(row.get("Minuto"))
            match_id = self._workbook_match_ids.get(workbook_id or -1)
            if not match_id or minute is None:
                continue
            score_home, score_away = self._parse_score(row.get("Placar"))
            stat_values = {
                "home_score": score_home,
                "away_score": score_away,
                "home_xg": self._as_decimal(row.get("xG Mandante")),
                "away_xg": self._as_decimal(row.get("xG Visitante")),
                "home_shots": self._as_int(row.get("Chutes M")),
                "away_shots": self._as_int(row.get("Chutes V")),
                "home_sot": self._as_int(row.get("No Alvo M")),
                "away_sot": self._as_int(row.get("No Alvo V")),
                "home_corners": self._as_int(row.get("Escanteios M")),
                "away_corners": self._as_int(row.get("Escanteios V")),
                "home_cards": self._as_int(row.get("Vermelhos M")),
                "away_cards": self._as_int(row.get("Vermelhos V")),
            }
            context = self._clean(row.get("Pressão/Contexto"))
            thesis = self._clean(row.get("Tese Sinalizada"))
            decision = self._clean(row.get("Decisão Simulada"))
            source = self._clean(row.get("Fonte"))
            observations = self._clean(row.get("Observações"))
            has_stats = any(value is not None for value in stat_values.values())
            has_context = any([context, thesis, decision])
            if has_stats or has_context:
                self.db.execute(
                    text(
                        """
                        DELETE FROM match_stat_snapshots
                        WHERE match_id = :match_id AND minute = :minute
                          AND extra->>'source' = :source
                        """
                    ),
                    {"match_id": match_id, "minute": minute, "source": self.SOURCE},
                )
                self.db.execute(
                    text(
                        """
                        INSERT INTO match_stat_snapshots (
                            match_id, minute, home_score, away_score, home_xg, away_xg,
                            home_shots, away_shots, home_sot, away_sot,
                            home_corners, away_corners, home_cards, away_cards,
                            extra, captured_at
                        ) VALUES (
                            :match_id, :minute, :home_score, :away_score, :home_xg, :away_xg,
                            :home_shots, :away_shots, :home_sot, :away_sot,
                            :home_corners, :away_corners, :home_cards, :away_cards,
                            CAST(:extra AS jsonb), :captured_at
                        )
                        """
                    ),
                    {
                        "match_id": match_id,
                        "minute": minute,
                        **stat_values,
                        "extra": json.dumps(
                            {
                                "source": self.SOURCE,
                                "checkpoint": self._clean(row.get("Checkpoint")),
                                "pressure_context": context,
                                "thesis": thesis,
                                "simulated_decision": decision,
                                "source_url": source,
                                "observations": observations,
                                "big_chances_home": self._as_int(row.get("Grandes Chances M")),
                                "big_chances_away": self._as_int(row.get("Grandes Chances V")),
                            },
                            ensure_ascii=False,
                        ),
                        "captured_at": self._captured_at(match_id, minute),
                    },
                )
                self.result.checkpoints += 1
            self._upsert_live_1x2_odds(
                match_id,
                minute,
                self._bookmaker_from_source(source),
                self._as_decimal(row.get("Odd Mandante")),
                self._as_decimal(row.get("Odd Empate")),
                self._as_decimal(row.get("Odd Visitante")),
            )

    def _import_strategies(self, workbook) -> None:
        if "Teses" not in workbook.sheetnames:
            return
        for row in self._rows(workbook["Teses"]):
            name = self._clean(row.get("Tese"))
            if not name:
                continue
            code = self._slug(name)
            description = " | ".join(
                part
                for part in [
                    f"janela={self._clean(row.get('Janela'))}" if self._clean(row.get("Janela")) else None,
                    f"criterio={self._clean(row.get('Critério inicial'))}" if self._clean(row.get("Critério inicial")) else None,
                    f"status={self._clean(row.get('Status'))}" if self._clean(row.get("Status")) else None,
                    f"objetivo={self._clean(row.get('Objetivo do backtest'))}" if self._clean(row.get("Objetivo do backtest")) else None,
                    f"n_min={self._clean(row.get('N mínimo desejado'))}" if self._clean(row.get("N mínimo desejado")) else None,
                    f"obs={self._clean(row.get('Observações'))}" if self._clean(row.get("Observações")) else None,
                ]
                if part
            )
            self.db.execute(
                text(
                    """
                    INSERT INTO strategies (code, name, description, version, is_active, created_at)
                    VALUES (:code, :name, :description, '1', true, now())
                    ON CONFLICT (code, version)
                    DO UPDATE SET name = EXCLUDED.name,
                                  description = EXCLUDED.description,
                                  is_active = true
                    """
                ),
                {"code": code, "name": name, "description": description or None},
            )
            self.result.strategies += 1

    def _upsert_match(
        self,
        competition_id: int,
        season_id: int,
        match_date: date,
        home_name: str,
        away_name: str,
        home_score: int | None,
        away_score: int | None,
        round_number: int | None,
    ) -> int:
        home_id = self._ensure_team(home_name)
        away_id = self._ensure_team(away_name)
        round_id = self._ensure_round(season_id, round_number) if round_number else None
        existing = self.db.execute(
            text(
                """
                SELECT id FROM matches
                WHERE season_id = :season_id
                  AND home_team_id = :home_id
                  AND away_team_id = :away_id
                  AND kickoff_at::date = :match_date
                ORDER BY id LIMIT 1
                """
            ),
            {"season_id": season_id, "home_id": home_id, "away_id": away_id, "match_date": match_date},
        ).scalar_one_or_none()
        status = "finished" if home_score is not None and away_score is not None else "scheduled"
        if existing:
            self.db.execute(
                text(
                    """
                    UPDATE matches
                    SET competition_id = :competition_id,
                        round_id = COALESCE(:round_id, round_id),
                        home_score = COALESCE(:home_score, home_score),
                        away_score = COALESCE(:away_score, away_score),
                        status = CASE WHEN :status = 'finished' THEN 'finished' ELSE status END,
                        winner_team_id = CASE
                            WHEN :home_score IS NULL OR :away_score IS NULL OR :home_score = :away_score THEN NULL
                            WHEN :home_score > :away_score THEN :home_id
                            ELSE :away_id
                        END,
                        updated_at = now()
                    WHERE id = :id
                    """
                ),
                {
                    "id": existing,
                    "competition_id": competition_id,
                    "round_id": round_id,
                    "home_score": home_score,
                    "away_score": away_score,
                    "status": status,
                    "home_id": home_id,
                    "away_id": away_id,
                },
            )
            self.result.matches_updated += 1
            return existing
        kickoff_at = datetime.combine(match_date, time(12, 0), tzinfo=UTC)
        match_id = self.db.execute(
            text(
                """
                INSERT INTO matches (
                    competition_id, season_id, round_id, home_team_id, away_team_id,
                    kickoff_at, status, home_score, away_score, winner_team_id,
                    created_at, updated_at
                ) VALUES (
                    :competition_id, :season_id, :round_id, :home_id, :away_id,
                    :kickoff_at, :status, :home_score, :away_score, :winner_team_id,
                    now(), now()
                ) RETURNING id
                """
            ),
            {
                "competition_id": competition_id,
                "season_id": season_id,
                "round_id": round_id,
                "home_id": home_id,
                "away_id": away_id,
                "kickoff_at": kickoff_at,
                "status": status,
                "home_score": home_score,
                "away_score": away_score,
                "winner_team_id": home_id if home_score is not None and away_score is not None and home_score > away_score else away_id if home_score is not None and away_score is not None and away_score > home_score else None,
            },
        ).scalar_one()
        self.result.matches += 1
        return match_id

    def _replace_goal_events(self, match_id: int, home: str, away: str, timeline: Any) -> None:
        raw = self._clean(timeline)
        if not raw:
            return
        self.db.execute(
            text("DELETE FROM match_events WHERE match_id = :id AND metadata->>'source' = :source"),
            {"id": match_id, "source": self.SOURCE},
        )
        for token in raw.split(";"):
            parsed = self._parse_goal_token(token.strip())
            if not parsed:
                continue
            code, minute, extra, subtype = parsed
            team_name = self.TEAM_CODES.get(code)
            team_id = None
            if team_name:
                team_id = self._ensure_team(team_name)
            self.db.execute(
                text(
                    """
                    INSERT INTO match_events (
                        match_id, team_id, period, minute, extra_minute,
                        event_type, event_subtype, value, metadata, created_at
                    ) VALUES (
                        :match_id, :team_id, :period, :minute, :extra,
                        'goal', :subtype, :value, CAST(:metadata AS jsonb), now()
                    )
                    """
                ),
                {
                    "match_id": match_id,
                    "team_id": team_id,
                    "period": "FIRST_HALF" if minute <= 45 else "SECOND_HALF",
                    "minute": minute,
                    "extra": extra,
                    "subtype": subtype,
                    "value": token.strip(),
                    "metadata": json.dumps({"source": self.SOURCE, "team_code": code, "home": home, "away": away}, ensure_ascii=False),
                },
            )
            self.result.goal_events += 1

    @staticmethod
    def _parse_goal_token(token: str) -> tuple[str, int, int | None, str | None] | None:
        match = re.search(r"^([A-Z]{3})\s+(\d+)(?:\+(\d+))?'", token.upper())
        if not match:
            return None
        code = match.group(1)
        minute = int(match.group(2))
        extra = int(match.group(3)) if match.group(3) else None
        lower = token.lower()
        subtype = "own_goal" if "contra" in lower else "penalty" if "pên" in lower or "pen" in lower else None
        return code, minute, extra, subtype

    def _upsert_1x2_odds(
        self,
        match_id: int,
        bookmaker_name: str,
        home_odd: Decimal | None,
        draw_odd: Decimal | None,
        away_odd: Decimal | None,
    ) -> None:
        if not any([home_odd, draw_odd, away_odd]):
            return
        bookmaker_id = self._ensure_bookmaker(bookmaker_name)
        market_id = self._ensure_market("1X2", "Resultado Final", "result")
        captured_at = self._captured_at(match_id, -1)
        for selection, odd in (("home", home_odd), ("draw", draw_odd), ("away", away_odd)):
            if odd is None:
                continue
            self._replace_odd(match_id, bookmaker_id, market_id, selection, odd, captured_at)
            self.result.pregame_odds += 1

    def _upsert_single_pregame_odd(self, match_id: int, bookmaker_name: str, selection_name: str, odd: Decimal) -> None:
        home_name, away_name = self.db.execute(
            text(
                """
                SELECT h.name, a.name FROM matches m
                JOIN teams h ON h.id = m.home_team_id
                JOIN teams a ON a.id = m.away_team_id
                WHERE m.id = :id
                """
            ),
            {"id": match_id},
        ).one()
        normalized = self._normalize(selection_name)
        if normalized == self._normalize(home_name):
            selection = "home"
        elif normalized == self._normalize(away_name):
            selection = "away"
        else:
            return
        bookmaker_id = self._ensure_bookmaker(bookmaker_name)
        market_id = self._ensure_market("1X2", "Resultado Final", "result")
        self._replace_odd(match_id, bookmaker_id, market_id, selection, odd, self._captured_at(match_id, -1))
        self.result.pregame_odds += 1

    def _upsert_live_1x2_odds(
        self,
        match_id: int,
        minute: int,
        bookmaker_name: str,
        home_odd: Decimal | None,
        draw_odd: Decimal | None,
        away_odd: Decimal | None,
    ) -> None:
        if not any([home_odd, draw_odd, away_odd]):
            return
        bookmaker_id = self._ensure_bookmaker(bookmaker_name)
        market_id = self._ensure_market("1X2_LIVE", "Resultado Final Live", "result")
        captured_at = self._captured_at(match_id, minute)
        for selection, odd in (("home", home_odd), ("draw", draw_odd), ("away", away_odd)):
            if odd is None:
                continue
            self._replace_odd(match_id, bookmaker_id, market_id, selection, odd, captured_at)
            self.result.live_odds += 1

    def _replace_odd(self, match_id: int, bookmaker_id: int, market_id: int, selection: str, odd: Decimal, captured_at: datetime) -> None:
        self.db.execute(
            text(
                """
                DELETE FROM odds_snapshots
                WHERE match_id = :match_id AND bookmaker_id = :bookmaker_id
                  AND market_id = :market_id AND selection = :selection
                  AND captured_at = :captured_at
                """
            ),
            {
                "match_id": match_id,
                "bookmaker_id": bookmaker_id,
                "market_id": market_id,
                "selection": selection,
                "captured_at": captured_at,
            },
        )
        self.db.execute(
            text(
                """
                INSERT INTO odds_snapshots (
                    match_id, bookmaker_id, market_id, selection, odd, captured_at
                ) VALUES (
                    :match_id, :bookmaker_id, :market_id, :selection, :odd, :captured_at
                )
                """
            ),
            {
                "match_id": match_id,
                "bookmaker_id": bookmaker_id,
                "market_id": market_id,
                "selection": selection,
                "odd": odd,
                "captured_at": captured_at,
            },
        )

    def _captured_at(self, match_id: int, minute: int) -> datetime:
        kickoff = self.db.execute(text("SELECT kickoff_at FROM matches WHERE id = :id"), {"id": match_id}).scalar_one()
        if minute < 0:
            return kickoff - timedelta(minutes=1)
        return kickoff + timedelta(minutes=minute)

    def _find_match_by_pair(self, season_id: int, home_name: str, away_name: str) -> int | None:
        return self.db.execute(
            text(
                """
                SELECT m.id FROM matches m
                JOIN teams h ON h.id = m.home_team_id
                JOIN teams a ON a.id = m.away_team_id
                WHERE m.season_id = :season_id
                  AND lower(h.name) = lower(:home)
                  AND lower(a.name) = lower(:away)
                ORDER BY m.kickoff_at DESC LIMIT 1
                """
            ),
            {"season_id": season_id, "home": home_name, "away": away_name},
        ).scalar_one_or_none()

    def _ensure_competition(self) -> int:
        existing = self.db.execute(
            text("SELECT id FROM competitions WHERE name = :name ORDER BY id LIMIT 1"),
            {"name": self.COMPETITION_NAME},
        ).scalar_one_or_none()
        if existing:
            return existing
        return self.db.execute(
            text(
                """
                INSERT INTO competitions (
                    name, short_name, country_code, competition_type, is_active, created_at, updated_at
                ) VALUES (:name, 'Brasileirão', 'BRA', 'league', true, now(), now())
                RETURNING id
                """
            ),
            {"name": self.COMPETITION_NAME},
        ).scalar_one()

    def _ensure_season(self, competition_id: int) -> int:
        existing = self.db.execute(
            text("SELECT id FROM seasons WHERE competition_id = :competition_id AND name = :name"),
            {"competition_id": competition_id, "name": self.SEASON_NAME},
        ).scalar_one_or_none()
        if existing:
            return existing
        return self.db.execute(
            text(
                """
                INSERT INTO seasons (
                    competition_id, name, start_date, end_date, is_current,
                    year_start, year_end, created_at, updated_at
                ) VALUES (
                    :competition_id, :name, DATE '2026-01-01', DATE '2026-12-31', true,
                    2026, 2026, now(), now()
                ) RETURNING id
                """
            ),
            {"competition_id": competition_id, "name": self.SEASON_NAME},
        ).scalar_one()

    def _ensure_round(self, season_id: int, round_number: int) -> int:
        existing = self.db.execute(
            text("SELECT id FROM rounds WHERE season_id = :season_id AND round_number = :round_number ORDER BY id LIMIT 1"),
            {"season_id": season_id, "round_number": round_number},
        ).scalar_one_or_none()
        if existing:
            return existing
        return self.db.execute(
            text(
                """
                INSERT INTO rounds (season_id, name, round_number)
                VALUES (:season_id, :name, :round_number) RETURNING id
                """
            ),
            {"season_id": season_id, "name": f"Rodada {round_number}", "round_number": round_number},
        ).scalar_one()

    def _ensure_team(self, name: str) -> int:
        existing = self.db.execute(
            text("SELECT id FROM teams WHERE lower(name) = lower(:name) ORDER BY id LIMIT 1"),
            {"name": name},
        ).scalar_one_or_none()
        if existing:
            return existing
        return self.db.execute(
            text(
                """
                INSERT INTO teams (name, country_code, is_active, created_at, updated_at)
                VALUES (:name, 'BRA', true, now(), now()) RETURNING id
                """
            ),
            {"name": name},
        ).scalar_one()

    def _ensure_bookmaker(self, name: str) -> int:
        existing = self.db.execute(text("SELECT id FROM bookmakers WHERE name = :name"), {"name": name}).scalar_one_or_none()
        if existing:
            return existing
        return self.db.execute(
            text("INSERT INTO bookmakers (name, country_code, is_active) VALUES (:name, 'BRA', true) RETURNING id"),
            {"name": name},
        ).scalar_one()

    def _ensure_market(self, code: str, name: str, category: str) -> int:
        existing = self.db.execute(text("SELECT id FROM markets WHERE code = :code"), {"code": code}).scalar_one_or_none()
        if existing:
            return existing
        return self.db.execute(
            text("INSERT INTO markets (code, name, category) VALUES (:code, :name, :category) RETURNING id"),
            {"code": code, "name": name, "category": category},
        ).scalar_one()

    @classmethod
    def _bookmaker_from_source(cls, value: Any) -> str:
        source = cls._normalize(value)
        if "bet365" in source:
            return "Bet365"
        if "betfair" in source:
            return "Betfair"
        if "sportingbet" in source:
            return "Sportingbet"
        if "footiqo" in source:
            return "Footiqo / 1xBet"
        return "Legacy Backtest Source"

    @staticmethod
    def _rows(sheet) -> list[dict[str, Any]]:
        iterator = sheet.iter_rows(values_only=True)
        try:
            headers = [str(value).strip() if value is not None else "" for value in next(iterator)]
        except StopIteration:
            return []
        rows: list[dict[str, Any]] = []
        for values in iterator:
            row = {headers[index]: value for index, value in enumerate(values) if index < len(headers) and headers[index]}
            if any(value not in (None, "") for value in row.values()):
                rows.append(row)
        return rows

    @staticmethod
    def _parse_score(value: Any) -> tuple[int | None, int | None]:
        if value is None or value == "":
            return None, None
        match = re.search(r"(\d+)\s*[xX\-:]\s*(\d+)", str(value))
        if not match:
            return None, None
        return int(match.group(1)), int(match.group(2))

    @staticmethod
    def _as_date(value: Any) -> date | None:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if value in (None, ""):
            return None
        text_value = str(value).strip()
        for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
            try:
                return datetime.strptime(text_value, fmt).date()
            except ValueError:
                pass
        return None

    @staticmethod
    def _as_int(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _as_decimal(value: Any) -> Decimal | None:
        if value in (None, ""):
            return None
        try:
            return Decimal(str(value).replace(",", "."))
        except (InvalidOperation, ValueError):
            return None

    @staticmethod
    def _clean(value: Any) -> str | None:
        if value in (None, ""):
            return None
        return str(value).strip()

    @staticmethod
    def _normalize(value: Any) -> str:
        import unicodedata

        raw = str(value or "").strip().lower()
        return "".join(char for char in unicodedata.normalize("NFKD", raw) if not unicodedata.combining(char))

    @classmethod
    def _slug(cls, value: str) -> str:
        normalized = cls._normalize(value)
        return re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")[:80]
