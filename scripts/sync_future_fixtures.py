import argparse
import asyncio
from datetime import UTC, date, datetime, timedelta

from apps.api.app.core.database import SessionLocal
from apps.api.app.providers.football_data_org import FootballDataOrgProvider
from apps.api.app.providers.contracts import ProviderSeason
from apps.api.app.services.ingestion import FootballIngestionService

BRAZIL_SERIE_A_FOOTBALL_DATA_ID = "2013"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync upcoming football fixtures into PostgreSQL"
    )
    parser.add_argument(
        "--competition-id",
        default=BRAZIL_SERIE_A_FOOTBALL_DATA_ID,
        help="football-data.org competition ID (default: 2013 / Brasileirão Série A)",
    )
    parser.add_argument(
        "--season-id",
        default=None,
        help="football-data.org season ID; current season is selected automatically when omitted",
    )
    parser.add_argument("--days-back", type=int, default=2)
    parser.add_argument("--days-ahead", type=int, default=45)
    parser.add_argument(
        "--allow-create-teams",
        action="store_true",
        help="Allow provider-only teams to be created instead of failing reconciliation",
    )
    return parser.parse_args()


def select_season(
    seasons: list[ProviderSeason],
    today: date,
    explicit_season_id: str | None = None,
) -> ProviderSeason:
    if explicit_season_id is not None:
        for season in seasons:
            if season.external_id == explicit_season_id:
                return season
        raise ValueError(f"Season '{explicit_season_id}' was not returned by provider")

    active = [
        season
        for season in seasons
        if season.start_date is not None
        and season.end_date is not None
        and season.start_date.date() <= today <= season.end_date.date()
    ]
    if len(active) == 1:
        return active[0]
    if len(active) > 1:
        raise ValueError(
            "More than one active season was returned; pass --season-id explicitly"
        )

    past_or_started = [
        season
        for season in seasons
        if season.start_date is not None and season.start_date.date() <= today
    ]
    if past_or_started:
        return max(past_or_started, key=lambda season: season.start_date)

    future = [season for season in seasons if season.start_date is not None]
    if future:
        return min(future, key=lambda season: season.start_date)

    raise ValueError("Provider returned no season with start_date")


async def main() -> None:
    args = parse_args()
    if args.days_back < 0 or args.days_ahead < 0:
        raise ValueError("days-back and days-ahead must be non-negative")

    provider = FootballDataOrgProvider()
    today = datetime.now(UTC).date()
    seasons = await provider.get_seasons(args.competition_id)
    season = select_season(seasons, today, args.season_id)
    date_from = today - timedelta(days=args.days_back)
    date_to = today + timedelta(days=args.days_ahead)

    with SessionLocal() as db:
        service = FootballIngestionService(db, provider)
        result = await service.sync_season(
            args.competition_id,
            season.external_id,
            fixture_date_from=date_from,
            fixture_date_to=date_to,
            strict_team_reconciliation=not args.allow_create_teams,
        )

    print(
        "future fixture sync completed:",
        {
            "provider": result.provider,
            "competition_external_id": result.competition_external_id,
            "season_external_id": result.season_external_id,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "records_created": result.records_created,
            "records_updated": result.records_updated,
            "matches_created": result.matches_created,
            "matches_updated": result.matches_updated,
        },
    )


if __name__ == "__main__":
    asyncio.run(main())
