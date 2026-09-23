from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime

from apps.api.app.domain.decision_journal import (
    DECISION_JOURNAL_VERSION,
    AnalysisType,
    DecisionJournalEntry,
)
from apps.api.app.domain.features import FeatureSet
from apps.api.app.domain.market_probability import MarketProbabilityResult
from apps.api.app.domain.poisson import PoissonResult
from apps.api.app.domain.risk_assessment import (
    RISK_ENGINE_VERSION,
    OpenPosition,
    RiskAssessment,
)
from apps.api.app.domain.value_assessment import VALUE_ENGINE_VERSION, ValueAssessment
from apps.api.app.services.feature_snapshot import semantic_hash as feature_semantic_hash
from apps.api.app.services.market_probability import MarketProbabilityEngine
from apps.api.app.services.market_probability_snapshot import (
    semantic_hash as market_probability_semantic_hash,
)
from apps.api.app.services.poisson_model import PoissonModel
from apps.api.app.services.poisson_snapshot import semantic_hash as poisson_semantic_hash
from apps.api.app.services.risk_engine import RiskEngine
from apps.api.app.services.value_assessment_snapshot import (
    semantic_hash as value_assessment_semantic_hash,
)
from apps.api.app.services.value_engine import ValueEngine

logger = logging.getLogger(__name__)


class DecisionJournalError(ValueError):
    pass


def _finite(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _stable_dataclass_payload(value: object) -> dict[str, object]:
    if not is_dataclass(value):
        raise TypeError("expected dataclass")
    payload = asdict(value)
    payload.pop("calculated_at", None)
    return payload


def _contains_non_finite(value: object) -> bool:
    if is_dataclass(value):
        return _contains_non_finite(asdict(value))
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(_contains_non_finite(item) for item in value.values())
    if isinstance(value, (tuple, list, set, frozenset)):
        return any(_contains_non_finite(item) for item in value)
    return False


def _digest(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class DecisionJournal:
    def record(
        self,
        *,
        features: FeatureSet,
        poisson: PoissonResult,
        probability: MarketProbabilityResult,
        value: ValueAssessment,
        risk: RiskAssessment,
        analysis_type: AnalysisType,
        match_type: str,
        competition: str | None = None,
        thesis: str | None = None,
        evaluated_at: datetime | None = None,
        positions: tuple[OpenPosition, ...] = (),
    ) -> DecisionJournalEntry:
        try:
            entry = self._record(
                features=features,
                poisson=poisson,
                probability=probability,
                value=value,
                risk=risk,
                analysis_type=analysis_type,
                match_type=match_type,
                competition=competition,
                thesis=thesis,
                evaluated_at=evaluated_at,
                positions=positions,
            )
        except DecisionJournalError as exc:
            logger.warning(
                "decision_journal_blocked",
                extra={
                    "match_id": getattr(risk, "match_id", None),
                    "market": str(getattr(risk, "market", None)),
                    "decision_journal_version": DECISION_JOURNAL_VERSION,
                    "reason": str(exc),
                },
            )
            raise
        return entry

    def _record(
        self,
        *,
        features: FeatureSet,
        poisson: PoissonResult,
        probability: MarketProbabilityResult,
        value: ValueAssessment,
        risk: RiskAssessment,
        analysis_type: AnalysisType,
        match_type: str,
        competition: str | None,
        thesis: str | None,
        evaluated_at: datetime | None,
        positions: tuple[OpenPosition, ...],
    ) -> DecisionJournalEntry:
        if not isinstance(features, FeatureSet):
            raise DecisionJournalError("INVALID_FEATURE_SET")
        if not isinstance(poisson, PoissonResult):
            raise DecisionJournalError("INVALID_POISSON_RESULT")
        if not isinstance(probability, MarketProbabilityResult):
            raise DecisionJournalError("INVALID_MARKET_PROBABILITY")
        if not isinstance(value, ValueAssessment):
            raise DecisionJournalError("INVALID_VALUE_ASSESSMENT")
        if not isinstance(risk, RiskAssessment):
            raise DecisionJournalError("INVALID_RISK_ASSESSMENT")
        if not isinstance(analysis_type, AnalysisType):
            raise DecisionJournalError("INVALID_ANALYSIS_TYPE")
        if not isinstance(match_type, str) or not match_type.strip():
            raise DecisionJournalError("INVALID_MATCH_TYPE")
        if value.value_engine_version != VALUE_ENGINE_VERSION:
            raise DecisionJournalError("INCOMPATIBLE_VALUE_ENGINE_VERSION")
        if risk.risk_engine_version != RISK_ENGINE_VERSION:
            raise DecisionJournalError("INCOMPATIBLE_RISK_ENGINE_VERSION")

        identity = (features.match_id, features.as_of)
        for artifact in (poisson, probability, value, risk):
            if (artifact.match_id, artifact.as_of) != identity:
                raise DecisionJournalError("IDENTITY_MISMATCH")
        if probability.market != value.market or value.market != risk.market:
            raise DecisionJournalError("MARKET_MISMATCH")
        if (
            poisson.model_version != probability.model_version
            or probability.model_version != value.model_version
            or value.model_version != risk.model_version
            or features.feature_engine_version != poisson.feature_engine_version
            or poisson.feature_engine_version != probability.feature_engine_version
            or probability.feature_engine_version != value.feature_engine_version
            or value.feature_engine_version != risk.feature_engine_version
            or probability.market_engine_version != value.market_engine_version
            or value.market_engine_version != risk.market_engine_version
            or value.value_engine_version != risk.value_engine_version
        ):
            raise DecisionJournalError("VERSION_MISMATCH")

        if any(
            _contains_non_finite(artifact)
            for artifact in (features, poisson, probability, value, risk, positions)
        ):
            raise DecisionJournalError("INVALID_NUMERIC_VALUE")
        if not _finite(risk.final_stake_units) or not _finite(risk.stake_amount):
            raise DecisionJournalError("INVALID_STAKE")

        try:
            feature_hash = feature_semantic_hash(features)
            poisson_hash = poisson_semantic_hash(poisson)
            probability_hash = market_probability_semantic_hash(probability)
            value_hash = value_assessment_semantic_hash(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise DecisionJournalError("INVALID_NUMERIC_VALUE") from exc

        # Recompute each deterministic predecessor from the artifact immediately
        # before it. This makes the journal fail closed when a caller combines
        # individually valid artifacts that never belonged to the same chain.
        try:
            expected_poisson = PoissonModel().calculate(features=features)
            expected_probability = MarketProbabilityEngine().calculate(
                poisson=poisson,
                market=probability.market,
            )
            expected_value = ValueEngine().calculate(
                probability=probability,
                market_odd=value.market_odd,
                uncertainty_margin_pp=value.uncertainty_margin_pp,
                odd_source=value.odd_source,
                odd_observed_at=value.odd_observed_at,
            )
            expected_risk = RiskEngine().calculate(
                value=value,
                bankroll_amount=risk.bankroll_amount,
                unit_percent=risk.unit_percent,
                positions=positions,
                exposure_known=risk.exposure_known,
            )
        except ValueError as exc:
            raise DecisionJournalError("PROVENANCE_RECOMPUTE_FAILED") from exc

        if poisson_semantic_hash(expected_poisson) != poisson_hash:
            raise DecisionJournalError("POISSON_PROVENANCE_MISMATCH")
        if market_probability_semantic_hash(expected_probability) != probability_hash:
            raise DecisionJournalError("MARKET_PROBABILITY_PROVENANCE_MISMATCH")
        if value_assessment_semantic_hash(expected_value) != value_hash:
            raise DecisionJournalError("VALUE_PROVENANCE_MISMATCH")
        if _stable_dataclass_payload(expected_risk) != _stable_dataclass_payload(risk):
            raise DecisionJournalError("RISK_PROVENANCE_MISMATCH")
        if risk.value_semantic_hash != value_hash:
            raise DecisionJournalError("VALUE_PROVENANCE_MISMATCH")
        if risk.value_decision != value.decision:
            raise DecisionJournalError("VALUE_DECISION_MISMATCH")
        semantic = {
            "match_id": risk.match_id,
            "as_of": risk.as_of.astimezone(UTC).isoformat(),
            "market": risk.market.value,
            "analysis_type": analysis_type.value,
            "decision_journal_version": DECISION_JOURNAL_VERSION,
            "feature_semantic_hash": feature_hash,
            "poisson_semantic_hash": poisson_hash,
            "market_probability_semantic_hash": probability_hash,
            "value_semantic_hash": value_hash,
            "risk_semantic_hash": risk.semantic_hash,
            "match_type": match_type.strip(),
            "competition": competition,
            "thesis": thesis,
        }
        digest = _digest(semantic)
        evaluated = evaluated_at or datetime.now(UTC)
        if evaluated.tzinfo is None or evaluated.utcoffset() is None:
            raise DecisionJournalError("INVALID_EVALUATED_AT")

        return DecisionJournalEntry(
            journal_entry_id=digest,
            match_id=risk.match_id,
            as_of=risk.as_of.astimezone(UTC),
            evaluated_at=evaluated.astimezone(UTC),
            market=risk.market,
            analysis_type=analysis_type,
            decision_journal_version=DECISION_JOURNAL_VERSION,
            semantic_hash=digest,
            feature_engine_version=features.feature_engine_version,
            model_name=poisson.model_name,
            model_version=poisson.model_version,
            market_engine_version=probability.market_engine_version,
            value_engine_version=value.value_engine_version,
            risk_engine_version=risk.risk_engine_version,
            feature_semantic_hash=feature_hash,
            poisson_semantic_hash=poisson_hash,
            market_probability_semantic_hash=probability_hash,
            value_semantic_hash=value_hash,
            risk_semantic_hash=risk.semantic_hash,
            competition=competition,
            match_type=match_type.strip(),
            thesis=thesis,
            p_model=value.p_model,
            p_cons=value.p_cons,
            p_break_even=value.p_break_even,
            fair_odds=value.fair_odds,
            market_odd=value.market_odd,
            odd_min=value.odd_min,
            edge_pp=value.edge_pp,
            ev_cons=value.ev_cons,
            confidence=value.confidence,
            value_decision=value.decision,
            risk_decision=risk.risk_decision,
            stake_units=risk.final_stake_units,
            stake_value=risk.stake_amount,
            exposure_known=risk.exposure_known,
            reason=risk.reason.value,
        )
