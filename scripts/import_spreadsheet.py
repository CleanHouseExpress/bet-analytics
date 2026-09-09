import argparse

from apps.api.app.core.database import SessionLocal
from apps.api.app.services.analises_bet_spreadsheet_import import AnalisesBetSpreadsheetImporter


def main() -> None:
    parser = argparse.ArgumentParser(description="Import the Analises BET project spreadsheet")
    parser.add_argument("file", help="Path to .xlsx file")
    parser.add_argument("--account", default="Betano")
    parser.add_argument("--competition", default="Imported Historical Data")
    parser.add_argument("--season", default="legacy")
    parser.add_argument(
        "--reprocess",
        action="store_true",
        help="Reprocess rows from the same file hash without duplicating canonical records",
    )
    args = parser.parse_args()

    with SessionLocal() as db:
        importer = AnalisesBetSpreadsheetImporter(db, account_name=args.account)
        method = importer.reprocess_file if args.reprocess else importer.import_file
        result = method(
            args.file,
            default_competition=args.competition,
            default_season=args.season,
        )

    print(
        "import complete:",
        f"run_id={result.run_id}",
        f"seen={result.rows_seen}",
        f"imported={result.rows_imported}",
        f"skipped={result.rows_skipped}",
        f"failed={result.rows_failed}",
    )


if __name__ == "__main__":
    main()
