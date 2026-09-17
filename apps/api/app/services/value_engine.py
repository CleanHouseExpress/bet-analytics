from __future__ import annotations

import logging
import math
from datetime import UTC, datetime
from time import perf_counter

from apps.api.app.domain.market_probability import (
    MARKET_ENGINE_VERSION,
    MarketProbabilityResult,
)
from apps.api.app.domain.value_assessment import (
    VALUE_ENGINE_VERSION,
    ValueAssessment,
    ValueDecision,
    ValueReason,
)

logger = logging.getLogger(__name__)

NUMERICAL_TOLERANCE = 1e-12
MIN_UNCERTAINTY_MARGIN_PP = 2.0
MAX_UNCERTAINTY_MARGIN_PP = 5.0
LOW_ODD_EXCEPTIONAL_FLOOR = 1.20


class ValueEngineError(ValueError):
    def __init__(self, reason: ValueReason):
        super().__init__(reason.value)
        self.reason = reason


def _finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


class ValueEngine:
    def calculate(
        self,
        *,
        probability: MarketProbabilityResult,
        market_odd: float | None,
        uncertainty_margin_pp: float | None,
        odd_source: str | None = None,
        odd_observed_at: datetime | None = None,
    ) -> ValueAssessment:
        started_at = perf_counter()
        try:
            result = self._calculate(
                probability=probability,
                market_odd=market_odd,
                uncertainty_margin_pp=uncertainty_margin_pp,
                odd_source=odd_source,
                odd_observed_at=odd_observed_at,
            )
        except ValueEngineError as exc:
            as_of = getattr(probability, "as_of", None)
            logger.warning(
                "value_assessment_blocked",
                extra={
                    "match_id": getattr(probability, "match_id", None),
                    "as_of": as_of.isoformat() if isinstance(as_of, datetime) else None,
                    "market": str(getattr(probability, "market", None)),
                    "value_engine_version": VALUE_ENGINE_VERSION,
                    "market_odd": market_odd,
                    "reason": exc.reason.value,
                    "duration_ms": (perf_counter() - started_at) * 1000,
                },
            )
            raise

        logger.info(
            "value_assessment_calculated",
            extra={
                "match_id": result.match_id,
                "as_of": result.as_of.isoformat(),
                "market": result.market.value,
                "value_engine_version": result.value_engine_version,
                "market_engine_version": result.market_engine_version,
                "market_odd": result.market_odd,
                "p_model": result.p_model,
                "p_cons": result.p_cons,
                "p_break_even": result.p_break_even,
                "edge_pp": result.edge_pp,
                "ev_cons": result.ev_cons,
                "odd_min": result.odd_min,
                "decision": result.decision.value,
                "reason": result.reason.value if result.reason else None,
                "duration_ms": (perf_counter() - started_at) * 1000,
            },
        )
        return result

    def _calculate(
        self,
        *,
        probability: MarketProbabilityResult,
        market_odd: float | None,
        uncertainty_margin_pp: float | None,
        odd_source: str | None,
        odd_observed_at: datetime | None,
    ) -> ValueAssessment:
        if not isinstance(probability, MarketProbabilityResult):
            raise ValueEngineError(ValueReason.INVALID_MARKET_PROBABILITY)
        if probability.market_engine_version != MARKET_ENGINE_VERSION:
            raise ValueEngineError(ValueReason.INCOMPATIBLE_MARKET_ENGINE_VERSION)
        if (
            probability.match_id <= 0
            or probability.as_of.tzinfo is None
            or probability.as_of.utcoffset() is None
            or not probability.model_name.strip()
            or not probability.model_version.strip()
            or not probability.feature_engine_version.strip()
        ):
            raise ValueEngineError(ValueReason.INVALID_MARKET_PROBABILITY)

        if not _finite_number(probability.p_model) or not 0.0 <= probability.p_model <= 1.0:
            raise ValueEngineError(ValueReason.INVALID_MARKET_PROBABILITY)
        p_model = float(probability.p_model)
        expected_fair_odds = None if p_model == 0.0 else 1.0 / p_model
        if p_model == 0.0:
            if probability.fair_odds is not None:
                raise ValueEngineError(ValueReason.INVALID_MARKET_PROBABILITY)
        elif (
            probability.fair_odds is None
            or not _finite_number(probability.fair_odds)
            or abs(probability.fair_odds - expected_fair_odds) > NUMERICAL_TOLERANCE
        ):
            raise ValueEngineError(ValueReason.INVALID_MARKET_PROBABILITY)

        if market_odd is None:
            raise ValueEngineError(ValueReason.MISSING_MARKET_ODD)
        if not _finite_number(market_odd) or market_odd <= 1.0:
            raise ValueEngineError(ValueReason.INVALID_MARKET_ODD)
        market_odd = float(market_odd)

        if (
            uncertainty_margin_pp is None
            or not _finite_number(uncertainty_margin_pp)
            or not MIN_UNCERTAINTY_MARGIN_PP
            <= float(uncertainty_margin_pp)
            <= MAX_UNCERTAINTY_MARGIN_PP
        ):
            raise ValueEngineError(ValueReason.INVALID_UNCERTAINTY_MARGIN)
        uncertainty_margin_pp = float(uncertainty_margin_pp)

        p_cons = max(0.0, p_model - uncertainty_margin_pp / 100.0)
        p_break_even = 1.0 / market_odd
        conservative_fair_odds = None if p_cons == 0.0 else 1.0 / p_cons
        edge_pp = (p_cons - p_break_even) * 100.0
        ev_cons = p_cons * market_odd - 1.0
        confidence = 1.0 - uncertainty_margin_pp / 100.0

        required_edge_pp: float | None = None
        odd_min: float | None = None
        reason: ValueReason | None = None

        if p_model < 0.70:
            decision = ValueDecision.NO_GO
            reason = ValueReason.PROBABILITY_BELOW_THRESHOLD
        elif p_model < 0.75:
            decision = ValueDecision.OBSERVAR
            reason = ValueReason.OBSERVATION_BAND
        else:
            if p_model < 0.80:
                candidate = ValueDecision.GO_CONDICIONAL
                required_edge_pp = 5.0
            elif p_model < 0.85:
                candidate = ValueDecision.GO
                required_edge_pp = 4.0
            elif p_model < 0.90:
                candidate = ValueDecision.GO_FORTE
                required_edge_pp = 3.0
            else:
                candidate = ValueDecision.GO_PROTEGIDO

            if candidate is ValueDecision.GO_PROTEGIDO:
                odd_min = 1.03 / p_cons
                if market_odd < LOW_ODD_EXCEPTIONAL_FLOOR and ev_cons < 0.03 - NUMERICAL_TOLERANCE:
                    decision = ValueDecision.NO_GO
                    reason = ValueReason.LOW_ODD_REQUIRES_EXCEPTIONAL_EVIDENCE
                elif (
                    p_cons <= p_break_even + NUMERICAL_TOLERANCE
                    or ev_cons < 0.03 - NUMERICAL_TOLERANCE
                ):
                    decision = ValueDecision.NO_GO
                    reason = ValueReason.NON_POSITIVE_CONSERVATIVE_EV
                else:
                    decision = candidate
            else:
                denominator = p_cons - required_edge_pp / 100.0
                edge_odd_min = None if denominator <= 0.0 else max(
                    1.0 / denominator,
                    1.0 / p_cons,
                )
                odd_min = (
                    None
                    if edge_odd_min is None
                    else max(edge_odd_min, LOW_ODD_EXCEPTIONAL_FLOOR)
                )
                if market_odd < LOW_ODD_EXCEPTIONAL_FLOOR:
                    decision = ValueDecision.NO_GO
                    reason = ValueReason.LOW_ODD_REQUIRES_EXCEPTIONAL_EVIDENCE
                elif ev_cons <= NUMERICAL_TOLERANCE:
                    decision = ValueDecision.NO_GO
                    reason = ValueReason.NON_POSITIVE_CONSERVATIVE_EV
                elif edge_pp + NUMERICAL_TOLERANCE < required_edge_pp:
                    decision = ValueDecision.NO_GO
                    reason = ValueReason.INSUFFICIENT_CONSERVATIVE_EDGE
                else:
                    decision = candidate

        return ValueAssessment(
            match_id=probability.match_id,
            as_of=probability.as_of.astimezone(UTC),
            calculated_at=datetime.now(UTC),
            market=probability.market,
            value_engine_version=VALUE_ENGINE_VERSION,
            market_engine_version=probability.market_engine_version,
            model_name=probability.model_name,
            model_version=probability.model_version,
            feature_engine_version=probability.feature_engine_version,
            market_odd=market_odd,
            odd_source=odd_source,
            odd_observed_at=odd_observed_at,
            p_model=p_model,
            uncertainty_margin_pp=uncertainty_margin_pp,
            p_cons=p_cons,
            p_break_even=p_break_even,
            fair_odds=expected_fair_odds,
            conservative_fair_odds=conservative_fair_odds,
            required_edge_pp=required_edge_pp,
            edge_pp=edge_pp,
            ev_cons=ev_cons,
            odd_min=odd_min,
            confidence=confidence,
            decision=decision,
            reason=reason,
        )
