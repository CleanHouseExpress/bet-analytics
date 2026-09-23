from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from apps.api.app.core.database import SessionLocal
from apps.api.app.domain.match_context import (
    CLASSIFIER_VERSION,
    AnalysisType,
    CompetitionFormat,
    MatchContext,
    MatchContextBlockReason,
)
from apps.api.app.providers.contracts import ProviderCompetition
from apps.api.app.services.ingestion import FootballIngestionService
from apps.api.app.services.match_context import MatchContextClassifier
from apps.api.app.services.match_context_snapshot import (
    persist_match_context_snapshot,
)


class _Provider:
    name = "bets-2-review-provider"


def _seed_match(db, *, source: str | None, competition_type: str = "league"):
    suffix = source or "unverified"
    now = datetime.now(UTC)
    competition_id = db.execute(
        text(
            """
            INSERT INTO competitions (
                name, country_code, competition_type, competition_type_source,
                created_at, updated_at
            ) VALUES (
                :name, 'BRA', :competition_type, :source, :now, :now
            )
            RETURNING id
            """
        ),
        {
            "name": f"BETS-2 Review {suffix} {now.timestamp()}",
            "competition_type": competition_type,
            "source": source,
            "now": now,
        },
    ).scalar_one()
    season_id = db.execute(
        text(
            """
            INSERT INTO seasons (
                competition_id, name, is_current, created_at, updated_at
            ) VALUES (:competition_id, '2199', false, :now, :now)
            RETURNING id
            """
        ),
        {"competition_id": competition_id, "now": now},
    ).scalar_one()
    home_id = db.execute(
        text(
            """
            INSERT INTO teams (name, country_code, created_at, updated_at)
            VALUES (:name, 'BRA', :now, :now)
            RETURNING id
            """
        ),
        {"name": f"BETS-2 Home {now.timestamp()}", "now": now},
    ).scalar_one()
    away_id = db.execute(
        text(
            """
            INSERT INTO teams (name, country_code, created_at, updated_at)
            VALUES (:name, 'BRA', :now, :now)
            RETURNING id
            """
        ),
        {"name": f"BETS-2 Away {now.timestamp()}", "now": now},
    ).scalar_one()
    kickoff = datetime(2199, 9, 23, 21, 0, tzinfo=UTC)
    match_id = db.execute(
        text(
            """
            INSERT INTO matches (
                competition_id, season_id, home_team_id, away_team_id,
                kickoff_at, status, created_at, updated_at
            ) VALUES (
                :competition_id, :season_id, :home_team_id, :away_team_id,
                :kickoff, 'scheduled', :now, :now
            )
            RETURNING id
            """
        ),
        {
            "competition_id": competition_id,
            "season_id": season_id,
            "home_team_id": home_id,
            "away_team_id": away_id,
            "kickoff": kickoff,
            "now": now,
        },
    ).scalar_one()
    return competition_id, season_id, home_id, away_id, match_id, kickoff


def test_unverified_legacy_league_fails_closed():
    with SessionLocal() as db:
        *_, match_id, kickoff = _seed_match(db, source=None)
        result = MatchContextClassifier(db).classify(
            match_id,
            as_of=kickoff - timedelta(hours=1),
        )
        assert result.context is None
        assert (
            result.block_reason
            is MatchContextBlockReason.UNVERIFIED_COMPETITION_FORMAT
        )
        db.rollback()


def test_verified_league_classifies_with_v2():
    with SessionLocal() as db:
        *_, match_id, kickoff = _seed_match(
            db,
            source="curated:bets-2-review-test",
        )
        result = MatchContextClassifier(db).classify(
            match_id,
            as_of=kickoff - timedelta(hours=1),
        )
        assert result.supported is True
        assert result.context is not None
        assert result.context.competition_format is CompetitionFormat.LEAGUE_POINTS
        assert result.context.analysis_type is AnalysisType.PRE_MATCH
        assert result.context.classifier_version == CLASSIFIER_VERSION
        assert CLASSIFIER_VERSION == "match-context-v2"
        db.rollback()


def test_verified_non_league_is_still_unsupported():
    with SessionLocal() as db:
        *_, match_id, kickoff = _seed_match(
            db,
            source="provider:test",
            competition_type="cup",
        )
        result = MatchContextClassifier(db).classify(
            match_id,
            as_of=kickoff - timedelta(hours=1),
        )
        assert result.context is None
        assert (
            result.block_reason
            is MatchContextBlockReason.UNSUPPORTED_COMPETITION_FORMAT
        )
        db.rollback()


def test_generic_ingestion_does_not_invent_league_format():
    service = FootballIngestionService.__new__(FootballIngestionService)
    service.provider = _Provider()
    competition_type, source = service._provider_competition_format(
        ProviderCompetition(
            external_id="unknown-format",
            name="Unknown Competition",
        )
    )
    assert competition_type == "unknown"
    assert source is None

    competition_type, source = service._provider_competition_format(
        ProviderCompetition(
            external_id="explicit-league",
            name="Explicit League",
            competition_type="LEAGUE",
        )
    )
    assert competition_type == "league"
    assert source == "provider:bets-2-review-provider"


def test_postgres_context_snapshot_is_concurrently_idempotent():
    cleanup = {
        "competition_id": None,
        "season_id": None,
        "home_id": None,
        "away_id": None,
        "match_id": None,
    }
    evaluation_key = "BETS-2-concurrent-review-finding"

    with SessionLocal() as db:
        db.execute(
            text(
                "DELETE FROM match_context_snapshots "
                "WHERE evaluation_key=:evaluation_key"
            ),
            {"evaluation_key": evaluation_key},
        )
        (
            cleanup["competition_id"],
            cleanup["season_id"],
            cleanup["home_id"],
            cleanup["away_id"],
            cleanup["match_id"],
            kickoff,
        ) = _seed_match(db, source="curated:bets-2-concurrency-test")
        db.commit()

    context = MatchContext(
        match_id=int(cleanup["match_id"]),
        competition_id=int(cleanup["competition_id"]),
        season_id=int(cleanup["season_id"]),
        competition_format=CompetitionFormat.LEAGUE_POINTS,
        analysis_type=AnalysisType.PRE_MATCH,
        classified_at=datetime(2199, 9, 23, 18, 0, tzinfo=UTC),
        as_of=kickoff - timedelta(hours=1),
    )

    def persist_once():
        with SessionLocal() as db:
            result = persist_match_context_snapshot(
                db,
                evaluation_key=evaluation_key,
                context=context,
            )
            db.commit()
            return result

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: persist_once(), range(2)))

        assert results[0]["evaluation_key"] == evaluation_key
        assert results[1]["evaluation_key"] == evaluation_key
        with SessionLocal() as db:
            count = db.execute(
                text(
                    "SELECT count(*) FROM match_context_snapshots "
                    "WHERE evaluation_key=:evaluation_key"
                ),
                {"evaluation_key": evaluation_key},
            ).scalar_one()
            assert count == 1
    finally:
        with SessionLocal() as db:
            db.execute(
                text(
                    "DELETE FROM match_context_snapshots "
                    "WHERE evaluation_key=:evaluation_key"
                ),
                {"evaluation_key": evaluation_key},
            )
            if cleanup["match_id"] is not None:
                db.execute(
                    text("DELETE FROM matches WHERE id=:id"),
                    {"id": cleanup["match_id"]},
                )
            if cleanup["season_id"] is not None:
                db.execute(
                    text("DELETE FROM seasons WHERE id=:id"),
                    {"id": cleanup["season_id"]},
                )
            for key in ("home_id", "away_id"):
                if cleanup[key] is not None:
                    db.execute(
                        text("DELETE FROM teams WHERE id=:id"),
                        {"id": cleanup[key]},
                    )
            if cleanup["competition_id"] is not None:
                db.execute(
                    text("DELETE FROM competitions WHERE id=:id"),
                    {"id": cleanup["competition_id"]},
                )
            db.commit()
