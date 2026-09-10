import argparse

from apps.api.app.core.database import SessionLocal
from apps.api.app.services.brasileirao_csv_import import BrasileiraoCsvImporter
from apps.api.app.services.brasileirao_existing_enrichment import (
    BrasileiraoExistingEnrichmentImporter,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import enriched Brasileirão season CSV")
    parser.add_argument("file", help="Path to CSV file")
    parser.add_argument(
        "--enrich-existing-only",
        action="store_true",
        help=(
            "Only enrich matches/teams that already exist; never creates competition, "
            "season, team, round, or match records"
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with SessionLocal() as db:
        if args.enrich_existing_only:
            result = BrasileiraoExistingEnrichmentImporter(db).import_file(args.file)
            print("=== BRASILEIRAO EXISTING-MATCH ENRICHMENT ===")
            print(f"rows_seen={result.rows_seen}")
            print(f"matches_enriched={result.matches_enriched}")
            print(f"rows_skipped_missing_match={result.rows_skipped_missing_match}")
            print(f"rows_skipped_ambiguous_match={result.rows_skipped_ambiguous_match}")
            print(f"statistics_upserted={result.statistics_upserted}")
            print(f"ht_updates={result.ht_updates}")
            print(f"goal_events_added={result.goal_events_added}")
            print(f"goal_events_reused={result.goal_events_reused}")
            print(f"teams_resolved={result.teams_resolved}")
            return

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
