import argparse

from apps.api.app.core.database import SessionLocal
from apps.api.app.services.brasileirao_backtest_import import BrasileiraoBacktestImporter


def main() -> None:
    parser = argparse.ArgumentParser(description="Import the Brasileirão backtest master workbook")
    parser.add_argument("file", help="Path to backtest_brasileirao_estrategias_teses_v1.xlsx")
    args = parser.parse_args()

    with SessionLocal() as db:
        result = BrasileiraoBacktestImporter(db).import_file(args.file)

    print("=== BRASILEIRAO BACKTEST IMPORT ===")
    print(f"matches_created={result.matches}")
    print(f"matches_updated={result.matches_updated}")
    print(f"goal_events={result.goal_events}")
    print(f"pregame_odds={result.pregame_odds}")
    print(f"live_odds={result.live_odds}")
    print(f"checkpoints={result.checkpoints}")
    print(f"strategies={result.strategies}")
    print(f"skipped={result.skipped}")


if __name__ == "__main__":
    main()
