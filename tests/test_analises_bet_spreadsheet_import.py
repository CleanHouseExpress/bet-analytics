from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from sqlalchemy import text

from apps.api.app.core.database import SessionLocal
from apps.api.app.services.analises_bet_spreadsheet_import import AnalisesBetSpreadsheetImporter


def _build_workbook(path: Path) -> None:
    workbook = Workbook()
    events = workbook.active
    events.title = "Eventos"
    events.append(
        [
            "ID",
            "Data/Hora",
            "Tipo de Evento",
            "Esporte",
            "Evento",
            "Mercado",
            "Seleção",
            "Tipo de Entrada",
            "Stake",
            "Odd",
            "Status",
            "Placar Final",
            "Retorno",
            "Lucro/Prejuízo",
            "ROI Evento",
            "SuperOdds",
            "Observações",
            "Momento da Entrada",
        ]
    )
    events.append(
        [
            "REAL-IMPORT-1",
            datetime(2026, 9, 7, 16, 48),
            "Aposta Esportiva",
            "Futebol",
            "Import Real Home - Import Real Away",
            "Criar Aposta",
            "1X + Menos de 3.5",
            "Under + Chance dupla",
            10,
            1.70,
            "Green",
            "1-0",
            17,
            7,
            0.70,
            "Não",
            "CA combinado",
            "Pré-jogo",
        ]
    )
    events.append(
        [
            "REAL-IMPORT-2",
            datetime(2026, 9, 7, 21, 10),
            "Aposta Esportiva",
            "Futebol",
            "Import Real Home - Import Real Away",
            "Total de Gols",
            "Menos de 1.5",
            "Under gols",
            10,
            1.40,
            "Red",
            "1-0",
            0,
            -10,
            -1,
            "Não",
            None,
            "Live",
        ]
    )

    movements = workbook.create_sheet("Movimentações")
    movements.append(["ID", "Data/Hora", "Tipo", "Valor", "Impacto no Caixa", "Observação"])
    movements.append(
        [
            "SNAP-REAL-INICIAL",
            datetime(2026, 9, 7, 0, 0),
            "Snapshot de saldo",
            100,
            0,
            "Saldo inicial real",
        ]
    )
    movements.append(
        [
            "SNAP-REAL-FINAL",
            datetime(2026, 9, 7, 23, 59),
            "Snapshot de saldo",
            117,
            0,
            "Saldo final real",
        ]
    )

    dashboard = workbook.create_sheet("Dashboard")
    dashboard.append(["Dashboard — derivado"])
    dashboard.append(["não deve virar aposta"])
    workbook.save(path)


def test_real_workbook_maps_events_matches_and_snapshots(tmp_path: Path) -> None:
    workbook_path = tmp_path / "analises-bet-real-import.xlsx"
    _build_workbook(workbook_path)

    with SessionLocal() as db:
        try:
            importer = AnalisesBetSpreadsheetImporter(db, account_name="Import Real Betano")
            first = importer.import_file(workbook_path)
            second = importer.import_file(workbook_path)

            assert first.rows_failed == 0
            assert second.rows_imported == 0

            bets = db.execute(
                text(
                    """
                    SELECT b.external_bet_id, b.match_id, b.result, s.is_live
                    FROM bets b
                    JOIN bet_selections s ON s.bet_id = b.id
                    WHERE b.external_bet_id IN ('REAL-IMPORT-1', 'REAL-IMPORT-2')
                    ORDER BY b.external_bet_id
                    """
                )
            ).all()
            assert len(bets) == 2
            assert bets[0].match_id == bets[1].match_id
            assert bets[0].result == "Green"
            assert bets[0].is_live is False
            assert bets[1].result == "Red"
            assert bets[1].is_live is True

            match_row = db.execute(
                text(
                    """
                    SELECT m.home_score, m.away_score, ht.name AS home, at.name AS away
                    FROM matches m
                    JOIN teams ht ON ht.id = m.home_team_id
                    JOIN teams at ON at.id = m.away_team_id
                    WHERE m.id = :match_id
                    """
                ),
                {"match_id": bets[0].match_id},
            ).one()
            assert match_row.home == "Import Real Home"
            assert match_row.away == "Import Real Away"
            assert match_row.home_score == 1
            assert match_row.away_score == 0

            snapshot = db.execute(
                text(
                    """
                    SELECT s.starting_balance, s.ending_balance
                    FROM daily_bankroll_snapshots s
                    JOIN betting_accounts a ON a.id = s.account_id
                    WHERE a.name = 'Import Real Betano'
                      AND s.snapshot_date = DATE '2026-09-07'
                    """
                )
            ).one()
            assert float(snapshot.starting_balance) == 100
            assert float(snapshot.ending_balance) == 117
        finally:
            db.rollback()
            db.execute(
                text(
                    "DELETE FROM import_rows WHERE sheet_name IN ('Eventos', 'Movimentações', 'Dashboard')"
                )
            )
            db.execute(
                text("DELETE FROM import_runs WHERE file_name = 'analises-bet-real-import.xlsx'")
            )
            db.execute(
                text(
                    """
                    DELETE FROM bets
                    WHERE external_bet_id IN ('REAL-IMPORT-1', 'REAL-IMPORT-2')
                    """
                )
            )
            db.execute(
                text(
                    """
                    DELETE FROM daily_bankroll_snapshots
                    WHERE account_id IN (
                        SELECT id FROM betting_accounts WHERE name = 'Import Real Betano'
                    )
                    """
                )
            )
            db.execute(text("DELETE FROM betting_accounts WHERE name = 'Import Real Betano'"))
            db.execute(
                text(
                    """
                    DELETE FROM matches
                    WHERE home_team_id IN (
                        SELECT id FROM teams WHERE name IN ('Import Real Home', 'Import Real Away')
                    )
                    OR away_team_id IN (
                        SELECT id FROM teams WHERE name IN ('Import Real Home', 'Import Real Away')
                    )
                    """
                )
            )
            db.execute(
                text("DELETE FROM teams WHERE name IN ('Import Real Home', 'Import Real Away')")
            )
            db.commit()
