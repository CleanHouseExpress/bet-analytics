import argparse

from apps.api.app.core.database import SessionLocal
from apps.api.app.services.brasileirao_csv_import import BrasileiraoCsvImporter


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import enriched Brasileirão season CSV")
    parser.add_argument("file", help="Path to CSV file")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with SessionLocal() as db:
        result = BrasileiraoCsvImporter(db).import_file(args.file)

    print("=== BRASILEIRAO CSV IMPORT ===")
    print(f"rows_seen={result.rows_seen}")
    print(f"matches_created={result.matches_created}")
    print(f"matches_updated={result.matches_updated}")
    print(f"statistics_upserted={result.statistics_upserted}")
    print(f"teams_created={result.teams_created}")
    print(f"teams_reused={result.teams_reused}")


if __name__ == "__main__":
    main()
