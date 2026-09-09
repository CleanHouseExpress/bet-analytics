from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from sqlalchemy import text

from apps.api.app.core.database import SessionLocal
from apps.api.app.services.brasileirao_backtest_import import BrasileiraoBacktestImporter


HOME = "Backtest Test Home"
AWAY = "Backtest Test Away"


def _build_workbook(path: Path) -> None:
    workbook = Workbook()

    detailed = workbook.active
    detailed.title = "Rodadas Detalhadas"
    detailed.append(
        [
            "Rodada",
            "Data",
            "Mandante",
            "Visitante",
            "Resultado",
            "Favorito pré-jogo",
            "Odd pré favorito",
            "Minutos dos gols",
            "Fonte odds",
            "Fonte gols",
            "Status",
        ]
    )
    detailed.append(
        [
            8,
            datetime(2026, 3, 22),
            HOME,
            AWAY,
            "2x1",
            HOME,
            1.8,
            "TST 12'; TST 45+2'; TST 78'",
            "https://example.com/betfair/odds",
            "https://example.com/goals",
            "Completo",
        ]
    )

    season = workbook.create_sheet("Temporada 2026")
    season.append(
        [
            "ID Fonte",
            "Data",
            "Mandante",
            "Visitante",
            "HT Mandante",
            "HT Visitante",
            "FT Mandante",
            "FT Visitante",
            "Resultado",
            "Odd Mandante Pré",
            "Odd Empate Pré",
            "Odd Visitante Pré",
            "Favorito Pré",
            "Fonte Odds",
            "Fonte Resultado",
            "Cobertura",
        ]
    )
    season.append(
        [
            999001,
            datetime(2026, 3, 22),
            HOME,
            AWAY,
            1,
            0,
            2,
            1,
            "Mandante",
            1.8,
            3.4,
            4.6,
            HOME,
            "https://footiqo.com/database/leagues/brazil-serie-a/",
            "https://example.com/result",
            "Completo: FT+HT+1X2",
        ]
    )

    games = workbook.create_sheet("Jogos")
    games.append(
        [
            "ID",
            "Competição",
            "Rodada",
            "Data",
            "Mandante",
            "Visitante",
            "Placar Final",
            "Gols Mandante",
            "Gols Visitante",
            "Resultado",
            "Favorito Pré-jogo",
            "Odd Mandante Pré",
            "Odd Empate Pré",
            "Odd Visitante Pré",
            "Fonte Resultado",
            "Fonte Odds",
            "Status Dados",
            "Observações",
        ]
    )
    games.append(
        [
            9001,
            "Brasileirão Série A 2026",
            8,
            None,
            HOME,
            AWAY,
            "2x1",
            2,
            1,
            "Mandante",
            HOME,
            1.8,
            3.4,
            4.6,
            "https://example.com/result",
            "https://example.com/bet365/odds",
            "Odds pré-jogo completas",
            "fixture id for checkpoint reconciliation",
        ]
    )

    checkpoints = workbook.create_sheet("Checkpoints")
    checkpoints.append(
        [
            "Jogo ID",
            "Checkpoint",
            "Minuto",
            "Placar",
            "Odd Mandante",
            "Odd Empate",
            "Odd Visitante",
            "xG Mandante",
            "xG Visitante",
            "Chutes M",
            "Chutes V",
            "No Alvo M",
            "No Alvo V",
            "Grandes Chances M",
            "Grandes Chances V",
            "Escanteios M",
            "Escanteios V",
            "Vermelhos M",
            "Vermelhos V",
            "Pressão/Contexto",
            "Tese Sinalizada",
            "Decisão Simulada",
            "Fonte",
            "Observações",
        ]
    )
    checkpoints.append(
        [
            9001,
            "60'",
            60,
            "1x0",
            2.2,
            3.0,
            5.5,
            1.1,
            0.4,
            9,
            4,
            4,
            1,
            2,
            0,
            5,
            2,
            0,
            0,
            "mandante pressionando",
            "Confirmação do favorito",
            "entrada simulada",
            "https://example.com/live",
            "checkpoint preenchido",
        ]
    )
    checkpoints.append([9001, "75'", 75] + [None] * 20 + ["A pesquisar"])

    theses = workbook.create_sheet("Teses")
    theses.append(
        [
            "Tese",
            "Janela",
            "Critério inicial",
            "Status",
            "Objetivo do backtest",
            "N mínimo desejado",
            "Observações",
        ]
    )
    theses.append(
        [
            "Confirmação do favorito - teste import",
            "25–35'",
            "Domínio confirmado",
            "Em teste",
            "ROI por faixa",
            100,
            "xG/grandes chances/pressão",
        ]
    )

    workbook.save(path)


def test_backtest_import_is_historical_and_idempotent(tmp_path: Path) -> None:
    workbook_path = tmp_path / "backtest-master.xlsx"
    _build_workbook(workbook_path)

    with SessionLocal() as db:
        try:
            first = BrasileiraoBacktestImporter(db).import_file(workbook_path)
            second = BrasileiraoBacktestImporter(db).import_file(workbook_path)

            assert first.matches == 1
            assert first.goal_events == 3
            assert first.checkpoints == 1
            assert first.strategies == 1
            assert second.matches == 0

            match = db.execute(
                text(
                    """
                    SELECT m.id, m.home_score, m.away_score, m.home_score_ht, m.away_score_ht,
                           r.round_number
                    FROM matches m
                    JOIN teams h ON h.id = m.home_team_id
                    JOIN teams a ON a.id = m.away_team_id
                    LEFT JOIN rounds r ON r.id = m.round_id
                    WHERE h.name = :home AND a.name = :away
                      AND m.kickoff_at::date = DATE '2026-03-22'
                    """
                ),
                {"home": HOME, "away": AWAY},
            ).one()
            assert (match.home_score, match.away_score) == (2, 1)
            assert (match.home_score_ht, match.away_score_ht) == (1, 0)
            assert match.round_number == 8

            event_count = db.execute(
                text(
                    """
                    SELECT count(*) FROM match_events
                    WHERE match_id = :id
                      AND metadata->>'source' = 'backtest_brasileirao_estrategias_teses_v1'
                    """
                ),
                {"id": match.id},
            ).scalar_one()
            assert event_count == 3

            snapshot_count = db.execute(
                text(
                    """
                    SELECT count(*) FROM match_stat_snapshots
                    WHERE match_id = :id
                      AND extra->>'source' = 'backtest_brasileirao_estrategias_teses_v1'
                    """
                ),
                {"id": match.id},
            ).scalar_one()
            assert snapshot_count == 1

            odds = db.execute(
                text("SELECT count(*) FROM odds_snapshots WHERE match_id = :id"),
                {"id": match.id},
            ).scalar_one()
            assert odds > 0

            strategies = db.execute(
                text(
                    """
                    SELECT count(*) FROM strategies
                    WHERE code = 'confirmacao_do_favorito_teste_import' AND version = '1'
                    """
                )
            ).scalar_one()
            assert strategies == 1
        finally:
            db.rollback()
            match_ids = db.execute(
                text(
                    """
                    SELECT m.id FROM matches m
                    JOIN teams h ON h.id = m.home_team_id
                    JOIN teams a ON a.id = m.away_team_id
                    WHERE h.name = :home AND a.name = :away
                    """
                ),
                {"home": HOME, "away": AWAY},
            ).scalars().all()
            for match_id in match_ids:
                db.execute(text("DELETE FROM matches WHERE id = :id"), {"id": match_id})
            db.execute(text("DELETE FROM teams WHERE name IN (:home, :away)"), {"home": HOME, "away": AWAY})
            db.execute(text("DELETE FROM strategies WHERE code = 'confirmacao_do_favorito_teste_import'"))
            db.commit()
