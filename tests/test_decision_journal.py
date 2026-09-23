import logging
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from apps.api.app.domain.decision_journal import AnalysisType
from apps.api.app.domain.features import (
    CompetitionBaseline,
    FeatureSet,
    FormWindow,
    StrengthFeatures,
)
from apps.api.app.domain.market_probability import Market
from apps.api.app.services.decision_journal import DecisionJournal, DecisionJournalError
from apps.api.app.services.market_probability import MarketProbabilityEngine
from apps.api.app.services.poisson_model import PoissonModel
from apps.api.app.services.risk_engine import RiskEngine
from apps.api.app.services.value_engine import ValueEngine


def _chain(*, odd: float = 1.50, exposure_known: bool = True):
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
        market_odd=odd,
        uncertainty_margin_pp=2,
        odd_source="journal-test",
        odd_observed_at=now,
    )
    risk = RiskEngine().calculate(
        value=value,
        bankroll_amount=500,
        exposure_known=exposure_known,
    )
    return features, poisson, probability, value, risk


def _record(*, odd: float = 1.50, evaluated_at=None):
    features, poisson, probability, value, risk = _chain(odd=odd)
    entry = DecisionJournal().record(
        features=features,
        poisson=poisson,
        probability=probability,
        value=value,
        risk=risk,
        analysis_type=AnalysisType.PRE_MATCH,
        match_type="LEAGUE",
        competition="Brasileirao",
        thesis="value-v1",
        evaluated_at=evaluated_at,
    )
    return entry, (features, poisson, probability, value, risk)


def test_full_chain_bets3_to_bets8_preserves_identity_and_provenance():
    entry, chain = _record()
    features, poisson, probability, value, risk = chain
    assert entry.match_id == features.match_id == poisson.match_id
    assert entry.match_id == probability.match_id == value.match_id == risk.match_id
    assert entry.market == probability.market == value.market == risk.market
    assert entry.feature_engine_version == features.feature_engine_version
    assert entry.model_version == poisson.model_version
    assert entry.market_engine_version == probability.market_engine_version
    assert entry.value_engine_version == value.value_engine_version
    assert entry.risk_engine_version == risk.risk_engine_version
    assert entry.value_semantic_hash == risk.value_semantic_hash
    assert entry.risk_semantic_hash == risk.semantic_hash
    assert all(
        len(digest) == 64
        for digest in (
            entry.feature_semantic_hash,
            entry.poisson_semantic_hash,
            entry.market_probability_semantic_hash,
            entry.value_semantic_hash,
            entry.risk_semantic_hash,
            entry.semantic_hash,
        )
    )


def test_journal_freezes_value_and_risk_outputs_without_recalculation():
    entry, chain = _record()
    _, _, _, value, risk = chain
    assert entry.p_model == value.p_model
    assert entry.p_cons == value.p_cons
    assert entry.p_break_even == value.p_break_even
    assert entry.fair_odds == value.fair_odds
    assert entry.market_odd == value.market_odd
    assert entry.odd_min == value.odd_min
    assert entry.edge_pp == value.edge_pp
    assert entry.ev_cons == value.ev_cons
    assert entry.confidence == value.confidence
    assert entry.value_decision == value.decision
    assert entry.risk_decision == risk.risk_decision
    assert entry.stake_units == risk.final_stake_units
    assert entry.stake_value == risk.stake_amount


def test_evaluated_at_does_not_change_semantic_identity():
    first, _ = _record(evaluated_at=datetime(2026, 9, 23, 13, tzinfo=UTC))
    second, _ = _record(evaluated_at=datetime(2026, 9, 23, 14, tzinfo=UTC))
    assert first.semantic_hash == second.semantic_hash
    assert first.journal_entry_id == second.journal_entry_id
    assert first.evaluated_at != second.evaluated_at


def test_material_odd_change_creates_new_journal_entry():
    first, _ = _record(odd=1.50)
    second, _ = _record(odd=1.55)
    assert first.value_semantic_hash != second.value_semantic_hash
    assert first.risk_semantic_hash != second.risk_semantic_hash
    assert first.semantic_hash != second.semantic_hash


def test_value_provenance_mismatch_fails_closed():
    features, poisson, probability, value, risk = _chain()
    invalid_risk = replace(risk, value_semantic_hash="0" * 64)
    with pytest.raises(DecisionJournalError, match="VALUE_PROVENANCE_MISMATCH"):
        DecisionJournal().record(
            features=features,
            poisson=poisson,
            probability=probability,
            value=value,
            risk=invalid_risk,
            analysis_type=AnalysisType.PRE_MATCH,
            match_type="LEAGUE",
        )


def test_identity_mismatch_fails_closed():
    features, poisson, probability, value, risk = _chain()
    invalid_risk = replace(risk, match_id=risk.match_id + 1)
    with pytest.raises(DecisionJournalError, match="IDENTITY_MISMATCH"):
        DecisionJournal().record(
            features=features,
            poisson=poisson,
            probability=probability,
            value=value,
            risk=invalid_risk,
            analysis_type=AnalysisType.PRE_MATCH,
            match_type="LEAGUE",
        )


def test_recorded_observability_event(caplog):
    caplog.set_level(logging.INFO)
    entry, _ = _record()
    record = next(
        item for item in caplog.records if item.message == "decision_journal_recorded"
    )
    assert record.journal_hash == entry.semantic_hash
    assert record.match_id == entry.match_id
    assert record.market == entry.market.value
    assert record.stake_units == entry.stake_units


def test_blocked_observability_event(caplog):
    features, poisson, probability, value, risk = _chain()
    caplog.set_level(logging.WARNING)
    with pytest.raises(DecisionJournalError):
        DecisionJournal().record(
            features=features,
            poisson=poisson,
            probability=probability,
            value=value,
            risk=replace(risk, value_semantic_hash="0" * 64),
            analysis_type=AnalysisType.PRE_MATCH,
            match_type="LEAGUE",
        )
    record = next(
        item for item in caplog.records if item.message == "decision_journal_blocked"
    )
    assert record.reason == "VALUE_PROVENANCE_MISMATCH"


@pytest.mark.parametrize(
    ("odd", "expected_decision"),
    [
        (1.05, "NO_GO"),
        (1.12, "OBSERVAR"),
        (1.20, "GO_PROTEGIDO"),
        (1.30, "GO"),
        (1.50, "GO_FORTE"),
    ],
)
def test_journal_records_value_decision_bands(odd, expected_decision):
    entry, _ = _record(odd=odd)
    assert entry.value_decision.value == expected_decision
    if expected_decision in {"NO_GO", "OBSERVAR"}:
        assert entry.stake_units == 0


def test_risk_semantic_change_creates_new_journal_entry():
    features, poisson, probability, value, risk = _chain(exposure_known=True)
    known = DecisionJournal().record(
        features=features,
        poisson=poisson,
        probability=probability,
        value=value,
        risk=risk,
        analysis_type=AnalysisType.PRE_MATCH,
        match_type="LEAGUE",
    )
    unknown_risk = RiskEngine().calculate(
        value=value,
        bankroll_amount=500,
        exposure_known=False,
    )
    unknown = DecisionJournal().record(
        features=features,
        poisson=poisson,
        probability=probability,
        value=value,
        risk=unknown_risk,
        analysis_type=AnalysisType.PRE_MATCH,
        match_type="LEAGUE",
    )
    assert known.value_semantic_hash == unknown.value_semantic_hash
    assert known.risk_semantic_hash != unknown.risk_semantic_hash
    assert known.semantic_hash != unknown.semantic_hash


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("p_model", float("nan"), "INVALID_NUMERIC_VALUE"),
        ("p_cons", float("inf"), "INVALID_NUMERIC_VALUE"),
        ("market_odd", float("nan"), "INVALID_NUMERIC_VALUE"),
        ("confidence", float("-inf"), "INVALID_NUMERIC_VALUE"),
    ],
)
def test_invalid_numeric_value_fails_closed(field, value, reason):
    features, poisson, probability, assessment, risk = _chain()
    invalid_value = replace(assessment, **{field: value})
    with pytest.raises(DecisionJournalError, match=reason):
        DecisionJournal().record(
            features=features,
            poisson=poisson,
            probability=probability,
            value=invalid_value,
            risk=risk,
            analysis_type=AnalysisType.PRE_MATCH,
            match_type="LEAGUE",
        )


def test_market_mismatch_fails_closed():
    features, poisson, probability, value, risk = _chain()
    invalid_risk = replace(risk, market=Market.BTTS_YES)
    with pytest.raises(DecisionJournalError, match="MARKET_MISMATCH"):
        DecisionJournal().record(
            features=features,
            poisson=poisson,
            probability=probability,
            value=value,
            risk=invalid_risk,
            analysis_type=AnalysisType.PRE_MATCH,
            match_type="LEAGUE",
        )


def test_version_mismatch_fails_closed():
    features, poisson, probability, value, risk = _chain()
    invalid_probability = replace(probability, model_version="poisson-v999")
    with pytest.raises(DecisionJournalError, match="VERSION_MISMATCH"):
        DecisionJournal().record(
            features=features,
            poisson=poisson,
            probability=invalid_probability,
            value=value,
            risk=risk,
            analysis_type=AnalysisType.PRE_MATCH,
            match_type="LEAGUE",
        )


def test_semantic_identity_includes_analysis_context():
    first, chain = _record()
    features, poisson, probability, value, risk = chain
    second = DecisionJournal().record(
        features=features,
        poisson=poisson,
        probability=probability,
        value=value,
        risk=risk,
        analysis_type=AnalysisType.LIVE,
        match_type="LEAGUE",
        competition="Brasileirao",
        thesis="value-v1",
        evaluated_at=first.evaluated_at,
    )
    assert first.semantic_hash != second.semantic_hash
