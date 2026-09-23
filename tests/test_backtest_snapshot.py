from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from apps.api.app.core.database import engine as postgres_engine
from apps.api.app.domain.backtest import (
    BACKTEST_VERSION,
    BacktestConfig,
    BacktestMetrics,
    BacktestPromotionStatus,
    BacktestRun,
)
from apps.api.app.services.backtest_snapshot import (
    BacktestSnapshotConflictError,
    persist_backtest_run,
)


def _run() -> BacktestRun:
    metrics = BacktestMetrics(
        evaluations=0,
        probability_evaluations=0,
        blocked_evaluations=0,
        missing_odd_evaluations=0,
        no_go_decisions=0,
        observe_decisions=0,
        go_decisions=0,
        bets=0,
        wins=0,
        losses=0,
        accuracy=None,
        brier_score=None,
        log_loss=None,
        baseline_brier_score=None,
        baseline_log_loss=None,
        expected_calibration_error=None,
        stake_total=0.0,
        profit_loss=0.0,
        roi_on_bankroll=0.0,
        yield_on_stake=None,
        max_drawdown=0.0,
        initial_bankroll=500.0,
        ending_bankroll=500.0,
        promotion_status=BacktestPromotionStatus.INSUFFICIENT_SAMPLE,
        calibration=(),
    )
    digest = "b" * 64
    return BacktestRun(
        run_id=digest,
        semantic_hash=digest,
        data_fingerprint="d" * 64,
        generated_at=datetime.now(UTC),
        backtest_version=BACKTEST_VERSION,
        config=BacktestConfig(
            competition_name="BETS-9 Snapshot Test",
            seasons=("2099",),
        ),
        evaluations=(),
        metrics=metrics,
        segments=(),
        equity_curve=(500.0,),
    )


def _delete_run(run_id: str) -> None:
    with postgres_engine.begin() as conn:
        conn.execute(
            text("DELETE FROM backtest_runs WHERE run_id=:run_id"),
            {"run_id": run_id},
        )


def test_backtest_snapshot_is_concurrency_safe():
    run = _run()
    _delete_run(run.run_id)

    def persist_once() -> int:
        with postgres_engine.begin() as conn:
            return persist_backtest_run(conn, run)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(lambda _: persist_once(), range(2)))
        assert ids[0] == ids[1]
        with postgres_engine.connect() as conn:
            assert conn.execute(
                text("SELECT count(*) FROM backtest_runs WHERE run_id=:run_id"),
                {"run_id": run.run_id},
            ).scalar_one() == 1
    finally:
        _delete_run(run.run_id)


def test_same_run_id_with_divergent_payload_conflicts():
    run = _run()
    _delete_run(run.run_id)
    try:
        with postgres_engine.begin() as conn:
            persist_backtest_run(conn, run)
        changed = replace(
            run,
            config=replace(run.config, initial_bankroll=600.0),
        )
        with postgres_engine.begin() as conn:
            with pytest.raises(
                BacktestSnapshotConflictError,
                match="BACKTEST_RUN_SEMANTIC_CONFLICT",
            ):
                persist_backtest_run(conn, changed)
    finally:
        _delete_run(run.run_id)
