import argparse

from apps.api.app.core.database import SessionLocal
from apps.api.app.services.spreadsheet_import import SpreadsheetImporter


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a BET project spreadsheet")
    parser.add_argument("file", help="Path to .xlsx file")
    parser.add_argument("--account", default="Betano")
    parser.add_argument("--competition", default="Imported Historical Data")
    parser.add_argument("--season", default="legacy")
    args = parser.parse_args()

    with SessionLocal() as db:
        result = SpreadsheetImporter(db, account_name=args.account).import_file(
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
