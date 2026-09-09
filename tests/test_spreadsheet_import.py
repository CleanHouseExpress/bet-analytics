from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from sqlalchemy import text

from apps.api.app.core.database import SessionLocal
from apps.api.app.services.spreadsheet_import import SpreadsheetImporter


def _build_workbook(path: Path) -> None:
    workbook = Workbook()
    history = workbook.active
    history.title = "Historico Jogos"
    history.append(
        [
            "Data",
            "Mandante",
            "Visitante",
            "Resultado",
            "Competição",
            "Temporada",
            "Gols/Minuto",
        ]
    )
    history.append(
        [
            datetime(2026, 8, 18, 20, 30),
            "Import Test Palmeiras",
            "Import Test Flamengo",
            "2-1",
            "Import Test League",
            "2026",
            (
                "Import Test Palmeiras 22'; Import Test Flamengo 35'; "
                "Import Test Palmeiras 81'"
            ),
        ]
    )

    bets = workbook.create_sheet("Apostas")
    bets.append(
        [
            "Data",
            "Valor Apostado",
            "Odd",
            "Ganhos",
            "Resultado Aposta",
            "Mercado",
            "Seleção",
            "ID Aposta",
        ]
    )
    bets.append(
        [
            datetime(2026, 8, 18, 22, 0),
            10.0,
            1.90,
            19.0,
            "Ganhou",
            "Ambas equipes marcam",
            "Sim",
            "IMPORT-TEST-1",
        ]
    )
    workbook.save(path)


def test_spreadsheet_import_is_idempotent(tmp_path: Path) -> None:
    workbook_path = tmp_path / "bet-import-test.xlsx"
    _build_workbook(workbook_path)

    with SessionLocal() as db:
        try:
            importer = SpreadsheetImporter(db, account_name="Import Test Betano")
            first = importer.import_file(workbook_path)
            second = importer.import_file(workbook_path)

            assert first.rows_seen == 2
            assert first.rows_imported == 2
            assert first.rows_failed == 0
            assert second.rows_seen == 2
            assert second.rows_imported == 0
            assert second.rows_skipped == 2

            bet_count = db.execute(
                text("SELECT count(*) FROM bets WHERE external_bet_id = 'IMPORT-TEST-1'")
            ).scalar_one()
            match_count = db.execute(
                text(
                    """
                    SELECT count(*)
                    FROM matches m
                    JOIN competitions c ON c.id = m.competition_id
                    WHERE c.name = 'Import Test League'
                    """
                )
            ).scalar_one()
            goal_count = db.execute(
                text(
                    """
                    SELECT count(*)
                    FROM match_events e
                    JOIN matches m ON m.id = e.match_id
                    JOIN competitions c ON c.id = m.competition_id
                    WHERE c.name = 'Import Test League' AND e.event_type = 'goal'
                    """
                )
            ).scalar_one()

            assert bet_count == 1
            assert match_count == 1
            assert goal_count == 3
        finally:
            db.rollback()
            db.execute(
                text("DELETE FROM import_rows WHERE sheet_name IN ('Historico Jogos', 'Apostas')")
            )
            db.execute(text("DELETE FROM import_runs WHERE file_name = 'bet-import-test.xlsx'"))
            db.execute(text("DELETE FROM bets WHERE external_bet_id = 'IMPORT-TEST-1'"))
            db.execute(text("DELETE FROM betting_accounts WHERE name = 'Import Test Betano'"))
            db.execute(
                text(
                    """
                    DELETE FROM matches
                    WHERE competition_id IN (
                        SELECT id FROM competitions WHERE name = 'Import Test League'
                    )
                    """
                )
            )
            db.execute(text("DELETE FROM teams WHERE name LIKE 'Import Test %'"))
            db.execute(
                text(
                    """
                    DELETE FROM seasons
                    WHERE competition_id IN (
                        SELECT id FROM competitions WHERE name = 'Import Test League'
                    )
                    """
                )
            )
            db.execute(text("DELETE FROM competitions WHERE name = 'Import Test League'"))
            db.commit()
