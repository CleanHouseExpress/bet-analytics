import argparse
import asyncio

from apps.api.app.core.database import SessionLocal
from apps.api.app.providers.sportmonks import SportmonksProvider
from apps.api.app.services.brazilian_leagues_sync import BrazilianLeaguesCatalogSync


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync Brazilian Série A/B/C/D competition, season and team catalogs"
    )
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument(
        "--series",
        default="A,B,C,D",
        help="Comma-separated series list, e.g. A,B,C,D",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    levels = [part.strip().upper() for part in args.series.split(",") if part.strip()]
    provider = SportmonksProvider()

    with SessionLocal() as db:
        service = BrazilianLeaguesCatalogSync(db, provider)
        results = await service.sync(args.year, levels)

    print("=== BRAZILIAN LEAGUES CATALOG SYNC ===")
    for result in results:
        print(
            f"Série {result.series}: "
            f"competition={result.provider_competition_id} "
            f"season={result.provider_season_id} "
            f"seen={result.teams_seen} "
            f"created={result.teams_created} "
            f"mapped={result.teams_mapped} "
            f"reused={result.teams_reused} "
            f"ambiguous={len(result.ambiguous)}"
        )
        for item in result.ambiguous:
            print(
                "  REVIEW "
                f"provider={item['provider_team']} "
                f"external_id={item['external_id']} "
                f"candidates={item['candidates']}"
            )


if __name__ == "__main__":
    asyncio.run(main())
