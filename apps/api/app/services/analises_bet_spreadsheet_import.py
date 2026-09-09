import hashlib
import json
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text

from apps.api.app.services.spreadsheet_import import SpreadsheetImporter


class AnalisesBetSpreadsheetImporter(SpreadsheetImporter):
    """Importer for the project's real Controle de Eventos e Balanço workbook."""

    HEADER_ALIASES = {
        **SpreadsheetImporter.HEADER_ALIASES,
        "date": SpreadsheetImporter.HEADER_ALIASES["date"] | {"data_hora", "data/hora"},
        "score": SpreadsheetImporter.HEADER_ALIASES["score"] | {"placar_final"},
        "result": SpreadsheetImporter.HEADER_ALIASES["result"] | {"status"},
        "event": {"evento", "event", "jogo", "partida"},
        "event_type": {"tipo_de_evento", "tipo_evento"},
        "entry_type": {"tipo_de_entrada", "tipo_entrada"},
        "profit_loss": {"lucro_prejuizo", "lucro/prejuizo", "profit_loss"},
        "observations": {"observacoes", "observações", "observacao", "observação", "notes"},
        "moment": {"momento_da_entrada", "momento_entrada"},
        "cash_impact": {"impacto_no_caixa", "impacto_caixa"},
    }

    def reprocess_file(
        self,
        path: str | Path,
        *,
        default_competition: str = "Imported Historical Data",
        default_season: str = "legacy",
    ):
        file_path = Path(path)
        file_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
        self.db.execute(
            text(
                """
                DELETE FROM import_rows
                WHERE import_run_id IN (
                    SELECT id FROM import_runs WHERE file_sha256 = :file_hash
                )
                """
            ),
            {"file_hash": file_hash},
        )
        self.db.commit()
        return self.import_file(
            file_path,
            default_competition=default_competition,
            default_season=default_season,
        )

    def _import_bet(
        self, canonical: dict[str, str], row: dict[str, Any], account_id: int
    ) -> int | None:
        stake = self._as_decimal(self._value(row, canonical, "stake"))
        if stake is None:
            return None

        placed_at = self._as_datetime(self._value(row, canonical, "date")) or datetime.now(UTC)
        external_id = self._value(row, canonical, "bet_id")
        odd = self._as_decimal(self._value(row, canonical, "odd"))
        return_amount = self._as_decimal(self._value(row, canonical, "return"))
        explicit_pl = self._as_decimal(self._value(row, canonical, "profit_loss"))
        result = self._normalize_bet_result(self._value(row, canonical, "result"))

        if explicit_pl == Decimal("0") or (
            explicit_pl is None
            and return_amount is not None
            and return_amount == stake
            and result is None
        ):
            return None

        if result is None and explicit_pl is not None:
            if explicit_pl > 0:
                result = "Green"
            elif explicit_pl < 0:
                result = "Red"

        profit_loss = explicit_pl
        if profit_loss is None and return_amount is not None:
            profit_loss = return_amount - stake

        event_name = self._value(row, canonical, "event")
        score = self._value(row, canonical, "score")
        match_id = self._ensure_event_match(event_name, placed_at, score)

        market = str(self._value(row, canonical, "market") or "legacy")
        selection = str(self._value(row, canonical, "selection") or market)
        moment = self._value(row, canonical, "moment")
        is_live = self._normalize(moment) == "live"
        notes = self._build_notes(canonical, row)

        fingerprint = hashlib.sha256(
            f"bet|{external_id}|{placed_at.isoformat()}|{stake}|{odd}|{selection}".encode()
        ).hexdigest()

        bet_id = self.db.execute(
            text(
                """
                INSERT INTO bets (
                    account_id, match_id, external_bet_id, placed_at, bet_type,
                    stake, total_odd, status, result, return_amount, profit_loss,
                    source, source_fingerprint, notes, created_at, updated_at
                ) VALUES (
                    :account_id, :match_id, :external_bet_id, :placed_at, 'single',
                    :stake, :odd, 'settled', :result, :return_amount, :profit_loss,
                    'spreadsheet', :fingerprint, :notes, now(), now()
                )
                ON CONFLICT (source_fingerprint)
                DO UPDATE SET match_id = EXCLUDED.match_id,
                              result = EXCLUDED.result,
                              return_amount = EXCLUDED.return_amount,
                              profit_loss = EXCLUDED.profit_loss,
                              notes = EXCLUDED.notes,
                              updated_at = now()
                RETURNING id
                """
            ),
            {
                "account_id": account_id,
                "match_id": match_id,
                "external_bet_id": str(external_id) if external_id else None,
                "placed_at": placed_at,
                "stake": stake,
                "odd": odd,
                "result": result,
                "return_amount": return_amount,
                "profit_loss": profit_loss,
                "fingerprint": fingerprint,
                "notes": notes,
            },
        ).scalar_one()

        self.db.execute(
            text("DELETE FROM bet_selections WHERE bet_id = :bet_id"),
            {"bet_id": bet_id},
        )
        self.db.execute(
            text(
                """
                INSERT INTO bet_selections (
                    bet_id, match_id, market, selection, odd, is_live, metadata
                ) VALUES (
                    :bet_id, :match_id, :market, :selection, :odd, :is_live,
                    CAST(:metadata AS jsonb)
                )
                """
            ),
            {
                "bet_id": bet_id,
                "match_id": match_id,
                "market": market,
                "selection": selection,
                "odd": odd,
                "is_live": is_live,
                "metadata": json.dumps(row, default=str),
            },
        )

        if result and return_amount is not None and profit_loss is not None:
            self.db.execute(
                text(
                    """
                    INSERT INTO bet_settlements (
                        bet_id, settled_at, result, return_amount, profit_loss, metadata
                    ) VALUES (
                        :bet_id, :settled_at, :result, :return_amount, :profit_loss,
                        CAST(:metadata AS jsonb)
                    )
                    ON CONFLICT (bet_id)
                    DO UPDATE SET result = EXCLUDED.result,
                                  return_amount = EXCLUDED.return_amount,
                                  profit_loss = EXCLUDED.profit_loss,
                                  metadata = EXCLUDED.metadata
                    """
                ),
                {
                    "bet_id": bet_id,
                    "settled_at": placed_at,
                    "result": result,
                    "return_amount": return_amount,
                    "profit_loss": profit_loss,
                    "metadata": json.dumps(row, default=str),
                },
            )

        return bet_id

    def _import_movement(
        self, canonical: dict[str, str], row: dict[str, Any], account_id: int
    ) -> int | None:
        movement_type = self._value(row, canonical, "movement_type")
        if "snapshot" in self._normalize(movement_type):
            return self._import_snapshot(canonical, row, account_id)
        return super()._import_movement(canonical, row, account_id)

    def _import_snapshot(
        self, canonical: dict[str, str], row: dict[str, Any], account_id: int
    ) -> int | None:
        balance = self._as_decimal(self._value(row, canonical, "stake"))
        occurred_at = self._as_datetime(self._value(row, canonical, "date"))
        if balance is None or occurred_at is None:
            return None

        record_id = str(row.get("id", ""))
        observations = str(self._value(row, canonical, "observations") or "")
        marker = self._normalize(f"{record_id} {observations}")
        is_initial = "inicial" in marker
        is_final = "final" in marker

        if not is_initial and not is_final:
            is_final = True

        snapshot_id = self.db.execute(
            text(
                """
                INSERT INTO daily_bankroll_snapshots (
                    account_id, snapshot_date, starting_balance, ending_balance,
                    created_at
                ) VALUES (
                    :account_id, :snapshot_date, :starting_balance, :ending_balance,
                    now()
                )
                ON CONFLICT (account_id, snapshot_date)
                DO UPDATE SET
                    starting_balance = COALESCE(
                        EXCLUDED.starting_balance,
                        daily_bankroll_snapshots.starting_balance
                    ),
                    ending_balance = COALESCE(
                        EXCLUDED.ending_balance,
                        daily_bankroll_snapshots.ending_balance
                    )
                RETURNING id
                """
            ),
            {
                "account_id": account_id,
                "snapshot_date": occurred_at.date(),
                "starting_balance": balance if is_initial else None,
                "ending_balance": balance if is_final else None,
            },
        ).scalar_one()
        return snapshot_id

    def _ensure_event_match(self, event_name: Any, placed_at: datetime, score: Any) -> int | None:
        if not event_name:
            return None
        teams = self._parse_event_teams(str(event_name))
        if teams is None:
            return None
        home_name, away_name = teams
        home_id = self._ensure_team(home_name)
        away_id = self._ensure_team(away_name)
        competition_id = self._ensure_competition("Imported Betting Events")
        season_id = self._ensure_season(competition_id, str(placed_at.year))
        home_score, away_score = self._parse_score(score)
        placeholder_kickoff = datetime.combine(placed_at.date(), time(12, 0), tzinfo=UTC)

        existing = self.db.execute(
            text(
                """
                SELECT id
                FROM matches
                WHERE season_id = :season_id
                  AND home_team_id = :home_team_id
                  AND away_team_id = :away_team_id
                  AND kickoff_at::date = :match_date
                ORDER BY id
                LIMIT 1
                """
            ),
            {
                "season_id": season_id,
                "home_team_id": home_id,
                "away_team_id": away_id,
                "match_date": placeholder_kickoff.date(),
            },
        ).scalar_one_or_none()

        if existing is not None:
            self.db.execute(
                text(
                    """
                    UPDATE matches
                    SET home_score = COALESCE(:home_score, home_score),
                        away_score = COALESCE(:away_score, away_score),
                        status = CASE
                            WHEN :home_score IS NOT NULL THEN 'finished'
                            ELSE status
                        END,
                        updated_at = now()
                    WHERE id = :match_id
                    """
                ),
                {
                    "match_id": existing,
                    "home_score": home_score,
                    "away_score": away_score,
                },
            )
            return existing

        return self.db.execute(
            text(
                """
                INSERT INTO matches (
                    competition_id, season_id, home_team_id, away_team_id,
                    kickoff_at, status, home_score, away_score, created_at, updated_at
                ) VALUES (
                    :competition_id, :season_id, :home_team_id, :away_team_id,
                    :kickoff_at, :status, :home_score, :away_score, now(), now()
                )
                RETURNING id
                """
            ),
            {
                "competition_id": competition_id,
                "season_id": season_id,
                "home_team_id": home_id,
                "away_team_id": away_id,
                "kickoff_at": placeholder_kickoff,
                "status": "finished" if home_score is not None else "scheduled",
                "home_score": home_score,
                "away_score": away_score,
            },
        ).scalar_one()

    def _build_notes(self, canonical: dict[str, str], row: dict[str, Any]) -> str | None:
        parts: list[str] = []
        for key in ("event_type", "entry_type", "moment", "observations"):
            value = self._value(row, canonical, key)
            if value not in (None, ""):
                parts.append(f"{key}={value}")
        return " | ".join(parts) or None

    @staticmethod
    def _parse_score(value: Any) -> tuple[int | None, int | None]:
        if isinstance(value, datetime | date):
            return value.day, value.month
        return SpreadsheetImporter._parse_score(value)

    @staticmethod
    def _parse_event_teams(event_name: str) -> tuple[str, str] | None:
        if " - " not in event_name:
            return None
        home, away = event_name.split(" - ", 1)
        home = home.strip()
        away = away.strip()
        if not home or not away:
            return None
        return home, away
