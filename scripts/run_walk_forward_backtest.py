from __future__ import annotations

import argparse
import json

from apps.api.app.core.database import SessionLocal
from apps.api.app.domain.backtest import BacktestConfig
from apps.api.app.domain.market_probability import Market
from apps.api.app.services.backtest_snapshot import persist_backtest_run
from apps.api.app.services.walk_forward_backtest import WalkForwardBacktest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run BETS-9 leakage-safe walk-forward backtest.",
    )
    parser.add_argument(
        "--season",
        dest="seasons",
        action="append",
        help="Season to include. Repeat for multiple seasons.",
    )
    parser.add_argument(
        "--market",
        dest="markets",
        action="append",
        choices=[market.value for market in Market],
        help="Market to include. Repeat for multiple markets.",
    )
    parser.add_argument("--initial-bankroll", type=float, default=500.0)
    parser.add_argument("--uncertainty-margin-pp", type=float, default=3.0)
    parser.add_argument("--as-of-offset-seconds", type=int, default=60)
    parser.add_argument("--min-sample-for-review", type=int, default=100)
    return parser


def main() -> None:
    args = _parser().parse_args()
    default = BacktestConfig()
    config = BacktestConfig(
        seasons=tuple(args.seasons) if args.seasons else default.seasons,
        markets=(
            tuple(Market(value) for value in args.markets)
            if args.markets
            else default.markets
        ),
        initial_bankroll=args.initial_bankroll,
        uncertainty_margin_pp=args.uncertainty_margin_pp,
        as_of_offset_seconds=args.as_of_offset_seconds,
        min_sample_for_review=args.min_sample_for_review,
    )
    with SessionLocal() as session:
        result = WalkForwardBacktest(session).run(config)
        persist_backtest_run(session.connection(), result)
        session.commit()

    print(
        json.dumps(
            {
                "run_id": result.run_id,
                "backtest_version": result.backtest_version,
                "data_fingerprint": result.data_fingerprint,
                "evaluations": result.metrics.evaluations,
                "probability_evaluations": result.metrics.probability_evaluations,
                "blocked_evaluations": result.metrics.blocked_evaluations,
                "missing_odd_evaluations": result.metrics.missing_odd_evaluations,
                "bets": result.metrics.bets,
                "brier_score": result.metrics.brier_score,
                "log_loss": result.metrics.log_loss,
                "baseline_brier_score": result.metrics.baseline_brier_score,
                "baseline_log_loss": result.metrics.baseline_log_loss,
                "profit_loss": result.metrics.profit_loss,
                "roi_on_bankroll": result.metrics.roi_on_bankroll,
                "yield_on_stake": result.metrics.yield_on_stake,
                "max_drawdown": result.metrics.max_drawdown,
                "initial_bankroll": result.metrics.initial_bankroll,
                "ending_bankroll": result.metrics.ending_bankroll,
                "promotion_status": result.metrics.promotion_status.value,
            },
            sort_keys=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
