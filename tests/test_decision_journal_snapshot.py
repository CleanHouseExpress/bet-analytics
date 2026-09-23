import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text

from apps.api.app.domain.decision_journal import (
    AnalysisType,
    DecisionSettlement,
    SettlementResult,
)
from apps.api.app.domain.features import (
    CompetitionBaseline,
    FeatureSet,
    FormWindow,
    StrengthFeatures,
)
from apps.api.app.domain.market_probability import Market
from apps.api.app.services.decision_journal import DecisionJournal
from apps.api.app.services.decision_journal_snapshot import (
    DecisionJournalConflictError,
    persist_decision_journal_entry,
)
from apps.api.app.services.market_probability import MarketProbabilityEngine
from apps.api.app.services.poisson_model import PoissonModel
from apps.api.app.services.risk_engine import RiskEngine
from apps.api.app.services.value_engine import ValueEngine


def _entry():
    now = datetime(2026, 9, 23, 12, tzinfo=UTC)
    form = FormWindow(10, 1.5, 1.0, 1.8, 5, 3, 2, True)
    features = FeatureSet(
        match_id=1808,
        as_of=now,
        calculated_at=now,
        context_classifier_version="match-context-v1",
        feature_engine_version="feature-engine-v1",
        home_last5=form,
        home_last10=form,
        away_last5=form,
        away_last10=form,
        home_home5=form,
        home_home10=form,
        away_away5=form,
        away_away10=form,
        competition_baseline=CompetitionBaseline(240, 1.5, 1.0, 2.5),
        strengths=StrengthFeatures(1.2, 0.8, 0.9, 1.1),
        reasons=(),
    )
    poisson = PoissonModel().calculate(features=features)
    probability = MarketProbabilityEngine().calculate(
        poisson=poisson,
        market=Market.TOTAL_GOALS_OVER_1_5,
    )
    value = ValueEngine().calculate(
        probability=probability,
        market_odd=1.50,
        uncertainty_margin_pp=2,
        odd_source="journal-snapshot-test",
        odd_observed_at=now,
    )
    risk = RiskEngine().calculate(
        value=value,
        bankroll_amount=500,
        exposure_known=True,
    )
    return DecisionJournal().record(
        features=features,
        poisson=poisson,
        probability=probability,
        value=value,
        risk=risk,
        analysis_type=AnalysisType.PRE_MATCH,
        match_type="LEAGUE",
        competition="Brasileirao",
        evaluated_at=now,
    )


def _setup():
    engine = create_engine("sqlite://")
    conn = engine.connect()
    conn.execute(
        text(
            "CREATE TABLE decision_journal_entries ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "journal_entry_id TEXT UNIQUE NOT NULL, "
            "semantic_hash TEXT UNIQUE NOT NULL, match_id INTEGER NOT NULL, "
            "as_of DATETIME NOT NULL, evaluated_at DATETIME NOT NULL, "
            "market TEXT NOT NULL, analysis_type TEXT NOT NULL, "
            "decision_journal_version TEXT NOT NULL, "
            "feature_engine_version TEXT NOT NULL, model_name TEXT NOT NULL, "
            "model_version TEXT NOT NULL, market_engine_version TEXT NOT NULL, "
            "value_engine_version TEXT NOT NULL, risk_engine_version TEXT NOT NULL, "
            "feature_semantic_hash TEXT NOT NULL, poisson_semantic_hash TEXT NOT NULL, "
            "market_probability_semantic_hash TEXT NOT NULL, "
            "value_semantic_hash TEXT NOT NULL, risk_semantic_hash TEXT NOT NULL, "
            "value_decision TEXT NOT NULL, risk_decision TEXT NOT NULL, "
            "stake_units FLOAT NOT NULL, stake_value FLOAT NOT NULL, "
            "exposure_known BOOLEAN NOT NULL, payload JSON NOT NULL)"
        )
    )
    return engine, conn


def test_persistence_is_idempotent_and_append_only():
    engine, conn = _setup()
    entry = _entry()
    first = persist_decision_journal_entry(conn, entry)
    second = persist_decision_journal_entry(
        conn,
        replace(entry, evaluated_at=entry.evaluated_at.replace(hour=13)),
    )
    assert first == second
    assert conn.execute(text("SELECT count(*) FROM decision_journal_entries")).scalar_one() == 1
    conn.close()
    engine.dispose()


def test_same_hash_divergent_payload_is_explicit_conflict():
    engine, conn = _setup()
    entry = _entry()
    persist_decision_journal_entry(conn, entry)
    raw = conn.execute(
        text("SELECT payload FROM decision_journal_entries WHERE semantic_hash=:hash"),
        {"hash": entry.semantic_hash},
    ).scalar_one()
    payload = json.loads(raw) if isinstance(raw, str) else dict(raw)
    payload["stake_value"] = 999
    conn.execute(
        text("UPDATE decision_journal_entries SET payload=:payload WHERE semantic_hash=:hash"),
        {"payload": json.dumps(payload), "hash": entry.semantic_hash},
    )
    with pytest.raises(
        DecisionJournalConflictError,
        match="DECISION_JOURNAL_SEMANTIC_CONFLICT",
    ):
        persist_decision_journal_entry(conn, entry)
    conn.close()
    engine.dispose()


def test_settlement_contract_does_not_mutate_decision_identity():
    entry = _entry()
    before = entry.semantic_hash
    settlement = DecisionSettlement(
        journal_entry_id=entry.journal_entry_id,
        result=SettlementResult.GREEN,
        profit_loss=5.0,
        closing_odd=1.42,
        clv=0.08,
        settled_at=datetime(2026, 9, 24, 1, tzinfo=UTC),
    )
    assert settlement.journal_entry_id == entry.journal_entry_id
    assert entry.semantic_hash == before
    assert not hasattr(entry, "result")
    assert not hasattr(entry, "profit_loss")
    assert not hasattr(entry, "closing_odd")
    assert not hasattr(entry, "clv")
