from datetime import UTC, datetime, timedelta
import math

import pytest
from sqlalchemy import text

from apps.api.app.core.database import SessionLocal
from apps.api.app.domain.match_context import AnalysisType, CompetitionFormat, MatchContext
from apps.api.app.services.feature_engine import FeatureEngine, FeatureEngineError
from apps.api.app.services.feature_snapshot import (
    FeatureSnapshotConflict,
    persist_feature_snapshot,
    semantic_hash,
)


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
    first_history_id = None
    for i in range(12):
        kickoff = base + timedelta(days=i)
        inserted = session.execute(text("INSERT INTO matches (competition_id,season_id,home_team_id,away_team_id,kickoff_at,status,home_score,away_score,created_at,updated_at) VALUES (:c,:s,:h,:a,:k,'finished',:hs,:as,:n,:n) RETURNING id"), {"c": comp,"s":season,"h": home if i % 2 == 0 else a,"a": a if i % 2 == 0 else home,"k":kickoff,"hs":2 if i % 2 == 0 else 1,"as":1 if i % 2 == 0 else 1,"n":now}).scalar_one()
        if first_history_id is None:
            first_history_id = inserted
        session.execute(text("INSERT INTO matches (competition_id,season_id,home_team_id,away_team_id,kickoff_at,status,home_score,away_score,created_at,updated_at) VALUES (:c,:s,:h,:a,:k,'finished',:hs,:as,:n,:n)"), {"c": comp,"s":season,"h": b if i % 2 == 0 else away,"a": away if i % 2 == 0 else b,"k":kickoff+timedelta(hours=1),"hs":1,"as":0 if i % 2 == 0 else 2,"n":now})
    target_kickoff = datetime(2096, 7, 1, tzinfo=UTC)
    target = session.execute(text("INSERT INTO matches (competition_id,season_id,home_team_id,away_team_id,kickoff_at,status,created_at,updated_at) VALUES (:c,:s,:h,:a,:k,'scheduled',:n,:n) RETURNING id"), {"c":comp,"s":season,"h":home,"a":away,"k":target_kickoff,"n":now}).scalar_one()
    session.commit()
    return target, comp, season, target_kickoff, home, away, a, first_history_id


def _insert_match(session, *, comp, season, home, away, kickoff, status='finished', hs=1, aws=0):
    n = datetime(2096, 1, 1, tzinfo=UTC)
    return session.execute(text("INSERT INTO matches (competition_id,season_id,home_team_id,away_team_id,kickoff_at,status,home_score,away_score,created_at,updated_at) VALUES (:c,:s,:h,:a,:k,:status,:hs,:aws,:n,:n) RETURNING id"), {"c":comp,"s":season,"h":home,"a":away,"k":kickoff,"status":status,"hs":hs,"aws":aws,"n":n}).scalar_one()


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
        target, comp, season, kickoff, home, _, a, _ = _seed(session)
        as_of = kickoff - timedelta(days=1)
        engine = FeatureEngine(session)
        context = _context(target, comp, season, as_of)
        before = engine.calculate(match_id=target, as_of=as_of, context=context)
        before_hash = semantic_hash(before)
        _insert_match(session, comp=comp, season=season, home=home, away=a, kickoff=as_of, hs=99, aws=0)
        _insert_match(session, comp=comp, season=season, home=home, away=a, kickoff=as_of+timedelta(hours=1), hs=99, aws=0)
        session.commit()
        after = engine.calculate(match_id=target, as_of=as_of, context=context)
        assert semantic_hash(after) == before_hash


def test_non_final_or_scoreless_matches_are_excluded():
    with SessionLocal() as session:
        target, comp, season, kickoff, home, _, a, _ = _seed(session)
        as_of = kickoff - timedelta(days=1)
        engine = FeatureEngine(session)
        context = _context(target, comp, season, as_of)
        before = engine.calculate(match_id=target, as_of=as_of, context=context)
        _insert_match(session, comp=comp, season=season, home=home, away=a, kickoff=as_of-timedelta(hours=3), status='scheduled', hs=99, aws=0)
        _insert_match(session, comp=comp, season=season, home=home, away=a, kickoff=as_of-timedelta(hours=2), status='finished', hs=None, aws=None)
        session.commit()
        after = engine.calculate(match_id=target, as_of=as_of, context=context)
        assert semantic_hash(after) == semantic_hash(before)


def test_target_match_is_explicitly_excluded_from_history_query():
    with SessionLocal() as session:
        target, comp, season, kickoff, home, _, a, _ = _seed(session)
        as_of = kickoff - timedelta(days=1)
        context = _context(target, comp, season, as_of)
        engine = FeatureEngine(session)
        before = engine.calculate(match_id=target, as_of=as_of, context=context)
        # Keep target kickoff valid (> as_of) while making its own row look like an otherwise
        # eligible final result. It must still not affect the historical feature sample.
        session.execute(text("UPDATE matches SET status='finished', home_score=99, away_score=0 WHERE id=:id"), {"id":target})
        session.commit()
        after = engine.calculate(match_id=target, as_of=as_of, context=context)
        assert after.match_id == target
        assert semantic_hash(after) == semantic_hash(before)


def test_baseline_isolated_by_competition_and_season():
    with SessionLocal() as session:
        target, comp, season, kickoff, home, _, a, _ = _seed(session)
        as_of = kickoff - timedelta(days=1)
        context = _context(target, comp, season, as_of)
        before = FeatureEngine(session).calculate(match_id=target, as_of=as_of, context=context)
        n = datetime(2096, 1, 1, tzinfo=UTC)
        other_comp = session.execute(text("INSERT INTO competitions (name,country_code,competition_type,created_at,updated_at) VALUES ('BETS-3 Other League','BRA','league',:n,:n) RETURNING id"), {"n":n}).scalar_one()
        other_season = session.execute(text("INSERT INTO seasons (competition_id,name,is_current,created_at,updated_at) VALUES (:c,'2096',false,:n,:n) RETURNING id"), {"c":other_comp,"n":n}).scalar_one()
        _insert_match(session, comp=other_comp, season=other_season, home=home, away=a, kickoff=as_of-timedelta(hours=1), hs=99, aws=99)
        old_season = session.execute(text("INSERT INTO seasons (competition_id,name,is_current,created_at,updated_at) VALUES (:c,'2095',false,:n,:n) RETURNING id"), {"c":comp,"n":n}).scalar_one()
        _insert_match(session, comp=comp, season=old_season, home=home, away=a, kickoff=as_of-timedelta(hours=2), hs=99, aws=99)
        session.commit()
        after = FeatureEngine(session).calculate(match_id=target, as_of=as_of, context=context)
        assert after.competition_baseline == before.competition_baseline
        assert after.home_last10 == before.home_last10


def test_strength_formulas_are_exact():
    with SessionLocal() as session:
        target, comp, season, kickoff, *_ = _seed(session)
        as_of = kickoff - timedelta(days=1)
        result = FeatureEngine(session).calculate(match_id=target, as_of=as_of, context=_context(target, comp, season, as_of))
        b = result.competition_baseline
        assert result.strengths.home_attack == pytest.approx(result.home_home10.gf_per_game / b.home_goals_per_game)
        assert result.strengths.home_defence_conceded == pytest.approx(result.home_home10.ga_per_game / b.away_goals_per_game)
        assert result.strengths.away_attack == pytest.approx(result.away_away10.gf_per_game / b.away_goals_per_game)
        assert result.strengths.away_defence_conceded == pytest.approx(result.away_away10.ga_per_game / b.home_goals_per_game)
        assert all(math.isfinite(v) for v in (result.strengths.home_attack, result.strengths.home_defence_conceded, result.strengths.away_attack, result.strengths.away_defence_conceded))


def test_zero_baseline_never_produces_nan_or_infinity():
    with SessionLocal() as session:
        target, comp, season, kickoff, *_ = _seed(session)
        as_of = kickoff - timedelta(days=1)
        session.execute(text("UPDATE matches SET home_score=0, away_score=0 WHERE competition_id=:c AND season_id=:s AND kickoff_at < :a"), {"c":comp,"s":season,"a":as_of})
        session.commit()
        result = FeatureEngine(session).calculate(match_id=target, as_of=as_of, context=_context(target, comp, season, as_of))
        assert result.strengths.home_attack is None
        assert result.strengths.home_defence_conceded is None
        assert result.strengths.away_attack is None
        assert result.strengths.away_defence_conceded is None
        assert 'INSUFFICIENT_COMPETITION_BASELINE' in {r.value for r in result.reasons}


def test_insufficient_history_exposes_counts_and_reasons():
    with SessionLocal() as session:
        target, comp, season, kickoff, *_ = _seed(session)
        as_of = datetime(2096, 6, 4, tzinfo=UTC)
        result = FeatureEngine(session).calculate(match_id=target, as_of=as_of, context=_context(target, comp, season, as_of))
        assert result.home_last10.games < 10
        assert result.away_last10.games < 10
        assert not result.home_last10.complete and not result.away_last10.complete
        reasons = {r.value for r in result.reasons}
        assert 'INSUFFICIENT_TEAM_HISTORY' in reasons
        assert 'INSUFFICIENT_HOME_HISTORY' in reasons
        assert 'INSUFFICIENT_AWAY_HISTORY' in reasons


def test_incompatible_match_context_is_blocked():
    with SessionLocal() as session:
        target, comp, season, kickoff, *_ = _seed(session)
        as_of = kickoff - timedelta(days=1)
        context = _context(target, comp, season, as_of)
        incompatible = MatchContext(
            match_id=context.match_id,
            competition_id=context.competition_id,
            season_id=context.season_id,
            competition_format=CompetitionFormat.KNOCKOUT_FIRST_LEG,
            analysis_type=context.analysis_type,
            classified_at=context.classified_at,
            as_of=context.as_of,
        )
        with pytest.raises(FeatureEngineError) as exc:
            FeatureEngine(session).calculate(match_id=target, as_of=as_of, context=incompatible)
        assert exc.value.reason.value == 'INCOMPATIBLE_MATCH_CONTEXT'


def test_persisted_snapshot_is_idempotent_and_retroactive_change_cannot_replace_evidence():
    with SessionLocal() as session:
        target, comp, season, kickoff, _, _, _, history_id = _seed(session)
        as_of = kickoff - timedelta(days=1)
        context = _context(target, comp, season, as_of)
        engine = FeatureEngine(session)
        original = engine.calculate(match_id=target, as_of=as_of, context=context)
        first = persist_feature_snapshot(session, evaluation_key='BETS-3-test-immutable', features=original)
        second = persist_feature_snapshot(session, evaluation_key='BETS-3-test-immutable', features=original)
        session.commit()
        assert first['semantic_hash'] == second['semantic_hash'] == semantic_hash(original)

        session.execute(text("UPDATE matches SET home_score = 9, away_score = 0 WHERE id = :id"), {"id": history_id})
        session.commit()
        changed = engine.calculate(match_id=target, as_of=as_of, context=context)
        assert semantic_hash(changed) != semantic_hash(original)
        with pytest.raises(FeatureSnapshotConflict):
            persist_feature_snapshot(session, evaluation_key='BETS-3-test-immutable', features=changed)
        stored_hash = session.execute(text("SELECT semantic_hash FROM feature_snapshots WHERE evaluation_key='BETS-3-test-immutable'")).scalar_one()
        assert stored_hash == semantic_hash(original)


def test_as_of_at_target_kickoff_is_rejected():
    with SessionLocal() as session:
        target, comp, season, kickoff, *_ = _seed(session)
        with pytest.raises(FeatureEngineError) as exc:
            FeatureEngine(session).calculate(match_id=target, as_of=kickoff, context=_context(target, comp, season, kickoff))
        assert exc.value.reason.value == 'INVALID_AS_OF'
