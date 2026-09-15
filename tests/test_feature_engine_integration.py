from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from apps.api.app.core.database import SessionLocal
from apps.api.app.domain.match_context import AnalysisType, CompetitionFormat, MatchContext
from apps.api.app.services.feature_engine import FeatureEngine, FeatureEngineError
from apps.api.app.services.feature_snapshot import semantic_hash


def _context(match_id, competition_id, season_id, as_of):
    return MatchContext(
        match_id=match_id,
        competition_id=competition_id,
        season_id=season_id,
        competition_format=CompetitionFormat.LEAGUE_POINTS,
        analysis_type=AnalysisType.PRE_MATCH,
        classified_at=as_of,
        as_of=as_of,
    )


def _seed(session):
    session.execute(text("DELETE FROM feature_snapshots WHERE evaluation_key LIKE 'BETS-3-test-%'"))
    now = datetime(2096, 1, 1, tzinfo=UTC)
    comp = session.execute(text("INSERT INTO competitions (name,country_code,competition_type,created_at,updated_at) VALUES ('BETS-3 Test League','BRA','league',:n,:n) RETURNING id"), {"n": now}).scalar_one()
    season = session.execute(text("INSERT INTO seasons (competition_id,name,is_current,created_at,updated_at) VALUES (:c,'2096',false,:n,:n) RETURNING id"), {"c": comp, "n": now}).scalar_one()
    teams = []
    for name in ('Home','Away','A','B'):
        teams.append(session.execute(text("INSERT INTO teams (name,country_code,created_at,updated_at) VALUES (:name,'BRA',:n,:n) RETURNING id"), {"name": f'BETS-3 {name}', "n": now}).scalar_one())
    home, away, a, b = teams
    base = datetime(2096, 6, 1, tzinfo=UTC)
    # 12 eligible matches for each target team, alternating venue and perspective.
    for i in range(12):
        kickoff = base + timedelta(days=i)
        session.execute(text("INSERT INTO matches (competition_id,season_id,home_team_id,away_team_id,kickoff_at,status,home_score,away_score,created_at,updated_at) VALUES (:c,:s,:h,:a,:k,'finished',:hs,:as,:n,:n)"), {"c": comp,"s":season,"h": home if i % 2 == 0 else a,"a": a if i % 2 == 0 else home,"k":kickoff,"hs":2 if i % 2 == 0 else 1,"as":1 if i % 2 == 0 else 1,"n":now})
        session.execute(text("INSERT INTO matches (competition_id,season_id,home_team_id,away_team_id,kickoff_at,status,home_score,away_score,created_at,updated_at) VALUES (:c,:s,:h,:a,:k,'finished',:hs,:as,:n,:n)"), {"c": comp,"s":season,"h": b if i % 2 == 0 else away,"a": away if i % 2 == 0 else b,"k":kickoff+timedelta(hours=1),"hs":1,"as":0 if i % 2 == 0 else 2,"n":now})
    target_kickoff = datetime(2096, 7, 1, tzinfo=UTC)
    target = session.execute(text("INSERT INTO matches (competition_id,season_id,home_team_id,away_team_id,kickoff_at,status,created_at,updated_at) VALUES (:c,:s,:h,:a,:k,'scheduled',:n,:n) RETURNING id"), {"c":comp,"s":season,"h":home,"a":away,"k":target_kickoff,"n":now}).scalar_one()
    session.commit()
    return target, comp, season, target_kickoff, home, away, a


def test_feature_engine_windows_splits_baseline_and_temporal_exclusion():
    with SessionLocal() as session:
        target, comp, season, kickoff, *_ = _seed(session)
        as_of = kickoff - timedelta(days=1)
        result = FeatureEngine(session).calculate(match_id=target, as_of=as_of, context=_context(target, comp, season, as_of))
        assert result.home_last5.games == 5 and result.home_last5.complete
        assert result.home_last10.games == 10 and result.home_last10.complete
        assert result.away_last5.games == 5 and result.away_last10.games == 10
        assert result.home_home5.games == 5
        assert result.away_away5.games == 5
        assert result.competition_baseline.games == 24
        assert result.strengths.home_attack is not None
        assert result.strengths.away_attack is not None


def test_match_at_or_after_as_of_does_not_enter_history_and_future_data_does_not_leak():
    with SessionLocal() as session:
        target, comp, season, kickoff, home, _, a = _seed(session)
        as_of = kickoff - timedelta(days=1)
        engine = FeatureEngine(session)
        context = _context(target, comp, season, as_of)
        before = engine.calculate(match_id=target, as_of=as_of, context=context)
        before_hash = semantic_hash(before)
        n = datetime(2096, 1, 1, tzinfo=UTC)
        session.execute(text("INSERT INTO matches (competition_id,season_id,home_team_id,away_team_id,kickoff_at,status,home_score,away_score,created_at,updated_at) VALUES (:c,:s,:h,:a,:k,'finished',99,0,:n,:n)"), {"c":comp,"s":season,"h":home,"a":a,"k":as_of,"n":n})
        session.execute(text("INSERT INTO matches (competition_id,season_id,home_team_id,away_team_id,kickoff_at,status,home_score,away_score,created_at,updated_at) VALUES (:c,:s,:h,:a,:k,'finished',99,0,:n,:n)"), {"c":comp,"s":season,"h":home,"a":a,"k":as_of+timedelta(hours=1),"n":n})
        session.commit()
        after = engine.calculate(match_id=target, as_of=as_of, context=context)
        assert semantic_hash(after) == before_hash


def test_as_of_at_target_kickoff_is_rejected():
    with SessionLocal() as session:
        target, comp, season, kickoff, *_ = _seed(session)
        try:
            FeatureEngine(session).calculate(match_id=target, as_of=kickoff, context=_context(target, comp, season, kickoff))
        except FeatureEngineError as exc:
            assert exc.reason.value == 'INVALID_AS_OF'
        else:
            raise AssertionError('expected INVALID_AS_OF')
