import argparse
import asyncio

from apps.api.app.core.database import SessionLocal
from apps.api.app.providers.sportmonks import SportmonksProvider
from apps.api.app.services.ingestion import FootballIngestionService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync one Sportmonks season into PostgreSQL")
    parser.add_argument("--competition-id", required=True, help="Sportmonks league/competition ID")
    parser.add_argument("--season-id", required=True, help="Sportmonks season ID")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    provider = SportmonksProvider()

    with SessionLocal() as db:
        service = FootballIngestionService(db, provider)
        result = await service.sync_season(args.competition_id, args.season_id)

    print(
        "sync completed:",
        {
            "provider": result.provider,
            "competition_external_id": result.competition_external_id,
            "season_external_id": result.season_external_id,
            "records_created": result.records_created,
            "records_updated": result.records_updated,
            "teams_created": result.teams_created,
            "matches_created": result.matches_created,
        },
    )


if __name__ == "__main__":
    asyncio.run(main())
