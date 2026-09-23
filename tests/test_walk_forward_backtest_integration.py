import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from apps.api.app.core.database import SessionLocal
from apps.api.app.domain.backtest import (
    BacktestConfig,
    BacktestEvaluationStatus,
    SeasonCoverageManifest,
)
from apps.api.app.domain.features import FeatureReason
from apps.api.app.domain.market_probability import Market
from apps.api.app.domain.match_context import (
    AnalysisType,
    CompetitionFormat,
    MatchContext,
)
from apps.api.app.services.backtest_snapshot import persist_backtest_run
from apps.api.app.services.feature_engine import FeatureEngine
from apps.api.app.services.walk_forward_backtest import (
    HistoricalFeatureEngine,
    WalkForwardBacktest,
)


def _insert_fixture(
    session,
    *,
    competition_id,
    season_id,
    round_id,
    home_team_id,
    away_team_id,
    kickoff_at,
    home_score,
    away_score,
    finished_at,
):
    return session.execute(
        text(
            """
            INSERT INTO matches (
                competition_id, season_id, round_id, home_team_id, away_team_id,
                kickoff_at, status, home_score, away_score, finished_at,
                created_at, updated_at
            ) VALUES (
                :competition_id, :season_id, :round_id, :home_team_id,
                :away_team_id, :kickoff_at, 'finished', :home_score,
                :away_score, :finished_at, :now, :now
            ) RETURNING id
            """
        ),
        {
            "competition_id": competition_id,
            "season_id": season_id,
            "round_id": round_id,
            "home_team_id": home_team_id,
            "away_team_id": away_team_id,
            "kickoff_at": kickoff_at,
            "home_score": home_score,
            "away_score": away_score,
            "finished_at": finished_at,
            "now": datetime.now(UTC),
        },
    ).scalar_one()


def _setup_walk_forward_history(session):
    now = datetime.now(UTC)
    competition_id = session.execute(
        text(
            """
            INSERT INTO competitions (
                name, country_code, competition_type, created_at, updated_at
            ) VALUES (
                'BETS-9 Walk Forward Integration', 'BRA', 'league', :now, :now
            ) RETURNING id
            """
        ),
        {"now": now},
    ).scalar_one()
    season_id = session.execute(
        text(
            """
            INSERT INTO seasons (
                competition_id, name, is_current, year_start, year_end,
                created_at, updated_at
            ) VALUES (
                :competition_id, '2099', false, 2099, 2099, :now, :now
            ) RETURNING id
            """
        ),
        {"competition_id": competition_id, "now": now},
    ).scalar_one()

    def team(name):
        return session.execute(
            text(
                """
                INSERT INTO teams (
                    name, country_code, created_at, updated_at
                ) VALUES (:name, 'BRA', :now, :now)
                RETURNING id
                """
            ),
            {"name": name, "now": now},
        ).scalar_one()

    home_team = team("BETS-9 Home")
    away_team = team("BETS-9 Away")
    home_opponents = [team(f"BETS-9 H Opp {index}") for index in range(1, 11)]
    away_opponents = [team(f"BETS-9 A Opp {index}") for index in range(1, 11)]

    base_kickoff = datetime(2099, 1, 2, 15, tzinfo=UTC)
    target_kickoff = base_kickoff + timedelta(days=70)
    imported_finished_at = target_kickoff + timedelta(days=30)

    for index in range(1, 11):
        round_id = session.execute(
            text(
                """
                INSERT INTO rounds (season_id, name, round_number)
                VALUES (:season_id, :name, :round_number)
                RETURNING id
                """
            ),
            {
                "season_id": season_id,
                "name": f"Rodada {index}",
                "round_number": index,
            },
        ).scalar_one()
        kickoff = base_kickoff + timedelta(days=(index - 1) * 7)
        _insert_fixture(
            session,
            competition_id=competition_id,
            season_id=season_id,
            round_id=round_id,
            home_team_id=home_team,
            away_team_id=home_opponents[index - 1],
            kickoff_at=kickoff,
            home_score=3,
            away_score=1,
            finished_at=imported_finished_at,
        )
        _insert_fixture(
            session,
            competition_id=competition_id,
            season_id=season_id,
            round_id=round_id,
            home_team_id=away_opponents[index - 1],
            away_team_id=away_team,
            kickoff_at=kickoff + timedelta(hours=2),
            home_score=1,
            away_score=3,
            finished_at=imported_finished_at,
        )

    target_round_id = session.execute(
        text(
            """
            INSERT INTO rounds (season_id, name, round_number)
            VALUES (:season_id, 'Rodada 11', 11)
            RETURNING id
            """
        ),
        {"season_id": season_id},
    ).scalar_one()
    target_match_id = _insert_fixture(
        session,
        competition_id=competition_id,
        season_id=season_id,
        round_id=target_round_id,
        home_team_id=home_team,
        away_team_id=away_team,
        kickoff_at=target_kickoff,
        home_score=2,
        away_score=1,
        finished_at=target_kickoff + timedelta(hours=3),
    )

    bookmaker_id = session.execute(
        text(
            """
            INSERT INTO bookmakers (name, country_code, is_active)
            VALUES ('BETS-9 Bookmaker', 'BRA', true)
            RETURNING id
            """
        )
    ).scalar_one()
    market_id = session.execute(
        text(
            """
            INSERT INTO markets (code, name, category)
            VALUES (
                'TOTAL_GOALS_OVER_1_5',
                'Total Goals Over 1.5',
                'goals'
            ) RETURNING id
            """
        )
    ).scalar_one()
    session.execute(
        text(
            """
            INSERT INTO odds_snapshots (
                match_id, bookmaker_id, market_id, selection, line, odd,
                captured_at
            ) VALUES (
                :match_id, :bookmaker_id, :market_id, 'over', 1.5, 1.60,
                :captured_at
            )
            """
        ),
        {
            "match_id": target_match_id,
            "bookmaker_id": bookmaker_id,
            "market_id": market_id,
            "captured_at": target_kickoff - timedelta(minutes=5),
        },
    )
    session.flush()
    return {
        "competition_id": competition_id,
        "season_id": season_id,
        "target_match_id": target_match_id,
        "target_kickoff": target_kickoff,
        "target_round_id": target_round_id,
        "home_team_id": home_team,
    }


def test_walk_forward_uses_event_chronology_without_ingestion_time_leakage():
    with SessionLocal() as session:
        ids = _setup_walk_forward_history(session)
        as_of = ids["target_kickoff"] - timedelta(seconds=60)
        late_opponent = session.execute(
            text(
                """
                INSERT INTO teams (
                    name, country_code, created_at, updated_at
                ) VALUES (
                    'BETS-9 Late Opponent', 'BRA', :now, :now
                ) RETURNING id
                """
            ),
            {"now": datetime.now(UTC)},
        ).scalar_one()
        _insert_fixture(
            session,
            competition_id=ids["competition_id"],
            season_id=ids["season_id"],
            round_id=ids["target_round_id"],
            home_team_id=ids["home_team_id"],
            away_team_id=late_opponent,
            kickoff_at=as_of - timedelta(minutes=30),
            home_score=9,
            away_score=0,
            finished_at=as_of + timedelta(hours=2),
        )

        context = MatchContext(
            match_id=ids["target_match_id"],
            competition_id=ids["competition_id"],
            season_id=ids["season_id"],
            competition_format=CompetitionFormat.LEAGUE_POINTS,
            analysis_type=AnalysisType.PRE_MATCH,
            classified_at=as_of,
            as_of=as_of,
        )

        production = FeatureEngine(session).calculate(
            match_id=ids["target_match_id"],
            as_of=as_of,
            context=context,
        )
        historical = HistoricalFeatureEngine(session).calculate(
            match_id=ids["target_match_id"],
            as_of=as_of,
        )

        assert production.home_last10.games == 0
        assert FeatureReason.INSUFFICIENT_TEAM_HISTORY in production.reasons
        assert historical.home_last10.games == 10
        assert historical.home_last10.gf_per_game == 3.0
        assert historical.away_last10.games == 10
        assert historical.home_home10.games == 10
        assert historical.away_away10.games == 10
        assert historical.reasons == ()


def test_walk_forward_full_chain_is_deterministic_and_persistable():
    with SessionLocal() as session:
        ids = _setup_walk_forward_history(session)
        config = BacktestConfig(
            competition_name="BETS-9 Walk Forward Integration",
            seasons=("2099",),
            markets=(Market.TOTAL_GOALS_OVER_1_5,),
            initial_bankroll=500,
            uncertainty_margin_pp=3,
            as_of_offset_seconds=60,
            min_sample_for_review=100,
            coverage_manifest=(
                SeasonCoverageManifest(
                    season="2099",
                    minimum_matches=21,
                    expected_teams=22,
                    minimum_matches_by_round=(
                        *((round_number, 2) for round_number in range(1, 11)),
                        (11, 1),
                    ),
                    source="tests/test_walk_forward_backtest_integration.py",
                ),
            ),
        )
        engine = WalkForwardBacktest(session)
        first = engine.run(config)
        second = engine.run(config)

        assert first.run_id == second.run_id
        assert first.data_fingerprint == second.data_fingerprint
        assert first.metrics.evaluations == 21
        target = next(
            evaluation
            for evaluation in first.evaluations
            if evaluation.match_id == ids["target_match_id"]
        )
        assert target.status is BacktestEvaluationStatus.EVALUATED
        assert target.p_model is not None
        assert target.market_odd == 1.6
        assert target.odd_source == "BETS-9 Bookmaker"
        assert target.journal_entry is not None
        assert target.feature_semantic_hash
        assert target.poisson_semantic_hash
        assert target.market_probability_semantic_hash
        assert target.value_semantic_hash
        assert target.risk_semantic_hash
        assert target.stake_value > 0
        assert target.profit_loss is not None

        feature_payload = json.loads(target.feature_payload)
        assert feature_payload["home_last10"]["games"] == 10
        assert feature_payload["away_last10"]["games"] == 10

        first_id = persist_backtest_run(session.connection(), first)
        second_id = persist_backtest_run(session.connection(), second)
        assert first_id == second_id
        assert session.execute(
            text(
                "SELECT count(*) FROM backtest_runs WHERE run_id=:run_id"
            ),
            {"run_id": first.run_id},
        ).scalar_one() == 1
        assert session.execute(
            text(
                """
                SELECT count(*) FROM backtest_evaluations
                WHERE run_id=:run_id
                """
            ),
            {"run_id": first.run_id},
        ).scalar_one() == 21
        session.rollback()
