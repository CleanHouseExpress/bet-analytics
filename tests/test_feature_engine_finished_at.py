from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from apps.api.app.core.database import SessionLocal
from apps.api.app.domain.match_context import AnalysisType, CompetitionFormat, MatchContext
from apps.api.app.services.feature_engine import FeatureEngine


def test_result_observed_after_as_of_is_excluded_from_historical_features():
    with SessionLocal() as session:
        now = datetime(2097, 1, 1, tzinfo=UTC)
        comp = session.execute(text("INSERT INTO competitions (name,country_code,competition_type,created_at,updated_at) VALUES ('BETS-3 FinishedAt','BRA','league',:n,:n) RETURNING id"), {"n": now}).scalar_one()
        season = session.execute(text("INSERT INTO seasons (competition_id,name,is_current,created_at,updated_at) VALUES (:c,'2097',false,:n,:n) RETURNING id"), {"c": comp, "n": now}).scalar_one()
        teams = [session.execute(text("INSERT INTO teams (name,country_code,created_at,updated_at) VALUES (:name,'BRA',:n,:n) RETURNING id"), {"name": f'BETS-3 FinishedAt {x}', "n": now}).scalar_one() for x in ('Home','Away','Other')]
        home, away, other = teams
        as_of = datetime(2097, 6, 10, 20, 30, tzinfo=UTC)

        # This match kicked off before as_of and is FINISHED in today's canonical row,
        # but the final result was only observed after as_of. It must not leak.
        session.execute(text("""
            INSERT INTO matches (
                competition_id,season_id,home_team_id,away_team_id,kickoff_at,status,
                home_score,away_score,finished_at,created_at,updated_at
            ) VALUES (:c,:s,:h,:a,:k,'finished',9,0,:finished,:n,:n)
        """), {"c":comp,"s":season,"h":home,"a":other,"k":as_of-timedelta(minutes=30),"finished":as_of+timedelta(hours=1),"n":now})

        target_kickoff = as_of + timedelta(days=1)
        target = session.execute(text("""
            INSERT INTO matches (
                competition_id,season_id,home_team_id,away_team_id,kickoff_at,status,
                created_at,updated_at
            ) VALUES (:c,:s,:h,:a,:k,'scheduled',:n,:n) RETURNING id
        """), {"c":comp,"s":season,"h":home,"a":away,"k":target_kickoff,"n":now}).scalar_one()
        session.commit()

        context = MatchContext(
            match_id=target,
            competition_id=comp,
            season_id=season,
            competition_format=CompetitionFormat.LEAGUE_POINTS,
            analysis_type=AnalysisType.PRE_MATCH,
            classified_at=as_of,
            as_of=as_of,
        )
        result = FeatureEngine(session).calculate(match_id=target, as_of=as_of, context=context)
        assert result.home_last10.games == 0
        assert result.competition_baseline.games == 0


def test_first_finished_observation_is_immutable():
    with SessionLocal() as session:
        now = datetime.now(UTC)
        comp = session.execute(text("INSERT INTO competitions (name,country_code,competition_type,created_at,updated_at) VALUES ('BETS-3 FinishedAt Trigger','BRA','league',:n,:n) RETURNING id"), {"n": now}).scalar_one()
        season = session.execute(text("INSERT INTO seasons (competition_id,name,is_current,created_at,updated_at) VALUES (:c,'2098',false,:n,:n) RETURNING id"), {"c":comp,"n":now}).scalar_one()
        teams = [session.execute(text("INSERT INTO teams (name,country_code,created_at,updated_at) VALUES (:name,'BRA',:n,:n) RETURNING id"), {"name":f'BETS-3 Trigger {x}',"n":now}).scalar_one() for x in ('H','A')]
        observed = now - timedelta(minutes=5)
        match_id = session.execute(text("""
            INSERT INTO matches (
                competition_id,season_id,home_team_id,away_team_id,kickoff_at,status,
                home_score,away_score,last_synced_at,created_at,updated_at
            ) VALUES (:c,:s,:h,:a,:k,'finished',2,1,:observed,:n,:n) RETURNING id
        """), {"c":comp,"s":season,"h":teams[0],"a":teams[1],"k":now-timedelta(hours=2),"observed":observed,"n":now}).scalar_one()
        session.commit()
        first = session.execute(text("SELECT finished_at FROM matches WHERE id=:id"), {"id":match_id}).scalar_one()
        assert first == observed

        later = now + timedelta(hours=1)
        session.execute(text("UPDATE matches SET last_synced_at=:later, finished_at=:later WHERE id=:id"), {"later":later,"id":match_id})
        session.commit()
        persisted = session.execute(text("SELECT finished_at FROM matches WHERE id=:id"), {"id":match_id}).scalar_one()
        assert persisted == observed
