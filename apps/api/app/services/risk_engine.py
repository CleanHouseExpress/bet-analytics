from __future__ import annotations

import logging
import math
from datetime import UTC, datetime

from apps.api.app.domain.market_probability import Market
from apps.api.app.domain.risk_assessment import (
    RISK_ENGINE_VERSION,
    OpenPosition,
    PositionStatus,
    RiskAssessment,
    RiskDecision,
    RiskReason,
)
from apps.api.app.domain.value_assessment import (
    VALUE_ENGINE_VERSION,
    ValueAssessment,
    ValueDecision,
)

logger = logging.getLogger(__name__)

DEFAULT_UNIT_PERCENT = 0.01
MAX_MATCH_EXPOSURE_UNITS = 3.0
MIN_STAKE_UNITS = 0.5
CORRELATION_FACTOR = 0.5

BASE_STAKE = {
    ValueDecision.NO_GO: 0.0,
    ValueDecision.OBSERVAR: 0.0,
    ValueDecision.GO_CONDICIONAL: 0.5,
    ValueDecision.GO: 1.0,
    ValueDecision.GO_FORTE: 1.5,
    ValueDecision.GO_PROTEGIDO: 1.0,
}

HIGH_CORRELATION = {
    frozenset((Market.TOTAL_GOALS_OVER_1_5, Market.TOTAL_GOALS_OVER_2_5)),
    frozenset((Market.TOTAL_GOALS_OVER_1_5, Market.BTTS_YES)),
    frozenset((Market.TOTAL_GOALS_OVER_2_5, Market.BTTS_YES)),
    frozenset((Market.TOTAL_GOALS_UNDER_3_5, Market.TOTAL_GOALS_UNDER_4_5)),
}
OPPOSING = {frozenset((Market.BTTS_YES, Market.BTTS_NO))}

EXPOSURE_WARNING = (
    "Stake calculada sem exposição confirmada registrada. Se já houver aposta aberta "
    "nesta partida ou mercado correlacionado fora do sistema, recalcular/reduzir a "
    "stake antes da entrada."
)


class RiskEngineError(ValueError):
    def __init__(self, reason: RiskReason):
        super().__init__(reason.value)
        self.reason = reason


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


class RiskEngine:
    def calculate(
        self,
        *,
        value: ValueAssessment,
        bankroll_amount: float,
        unit_percent: float = DEFAULT_UNIT_PERCENT,
        positions: tuple[OpenPosition, ...] = (),
        exposure_known: bool = False,
        wallet_id: str | None = None,
    ) -> RiskAssessment:
        try:
            result = self._calculate(
                value=value,
                bankroll_amount=bankroll_amount,
                unit_percent=unit_percent,
                positions=positions,
                exposure_known=exposure_known,
                wallet_id=wallet_id,
            )
        except RiskEngineError as exc:
            logger.warning(
                "risk_assessment_blocked",
                extra={
                    "match_id": getattr(value, "match_id", None),
                    "market": str(getattr(value, "market", None)),
                    "risk_engine_version": RISK_ENGINE_VERSION,
                    "reason": exc.reason.value,
                },
            )
            raise
        logger.info(
            "risk_assessment_calculated",
            extra={
                "match_id": result.match_id,
                "market": result.market.value,
                "risk_engine_version": result.risk_engine_version,
                "value_decision": result.value_decision.value,
                "bankroll_amount": result.bankroll_amount,
                "unit_value": result.unit_value,
                "base_stake_units": result.base_stake_units,
                "current_match_exposure_units": result.current_match_exposure_units,
                "correlation_adjustment": result.correlation_adjustment,
                "final_stake_units": result.final_stake_units,
                "stake_amount": result.stake_amount,
                "exposure_known": result.exposure_known,
                "risk_decision": result.risk_decision.value,
                "reason": result.reason.value,
            },
        )
        return result

    def _calculate(
        self,
        *,
        value: ValueAssessment,
        bankroll_amount: float,
        unit_percent: float,
        positions: tuple[OpenPosition, ...],
        exposure_known: bool,
        wallet_id: str | None,
    ) -> RiskAssessment:
        if not isinstance(value, ValueAssessment):
            raise RiskEngineError(RiskReason.INVALID_VALUE_ASSESSMENT)
        if value.value_engine_version != VALUE_ENGINE_VERSION:
            raise RiskEngineError(RiskReason.INCOMPATIBLE_VALUE_ENGINE_VERSION)
        if value.match_id <= 0 or value.as_of.tzinfo is None or value.as_of.utcoffset() is None:
            raise RiskEngineError(RiskReason.INVALID_VALUE_ASSESSMENT)
        if not _finite(bankroll_amount) or float(bankroll_amount) <= 0:
            raise RiskEngineError(RiskReason.INVALID_BANKROLL)
        if not _finite(unit_percent) or float(unit_percent) <= 0:
            raise RiskEngineError(RiskReason.INVALID_UNIT_PERCENT)

        bankroll = float(bankroll_amount)
        unit_percent_value = float(unit_percent)
        unit_value = bankroll * unit_percent_value
        base = BASE_STAKE[value.decision]

        placed: list[OpenPosition] = []
        for position in positions:
            if not isinstance(position, OpenPosition):
                raise RiskEngineError(RiskReason.INVALID_POSITION)
            if position.status is not PositionStatus.PLACED:
                continue
            if position.match_id != value.match_id:
                raise RiskEngineError(RiskReason.INVALID_POSITION)
            if wallet_id is not None and position.wallet_id != wallet_id:
                raise RiskEngineError(RiskReason.INVALID_POSITION)
            if not position.position_id.strip() or not _finite(position.stake_units) or position.stake_units < 0:
                raise RiskEngineError(RiskReason.INVALID_POSITION)
            placed.append(position)

        current = math.fsum(position.stake_units for position in placed)
        remaining = max(0.0, MAX_MATCH_EXPOSURE_UNITS - current)
        warning = None if exposure_known else EXPOSURE_WARNING

        if base == 0.0:
            return self._result(value, bankroll, unit_percent_value, unit_value, base, current, remaining, 1.0, 0.0, exposure_known, warning, RiskDecision.NO_POSITION, RiskReason.VALUE_NOT_GO)
        if remaining <= 0.0:
            return self._result(value, bankroll, unit_percent_value, unit_value, base, current, remaining, 1.0, 0.0, exposure_known, warning, RiskDecision.NO_POSITION, RiskReason.MATCH_EXPOSURE_LIMIT_REACHED)

        correlated = any(frozenset((value.market, p.market)) in HIGH_CORRELATION for p in placed)
        opposing = any(frozenset((value.market, p.market)) in OPPOSING for p in placed)
        factor = CORRELATION_FACTOR if correlated or opposing else 1.0
        adjusted = base * factor
        final = min(adjusted, remaining)

        if final < MIN_STAKE_UNITS:
            return self._result(value, bankroll, unit_percent_value, unit_value, base, current, remaining, factor, 0.0, exposure_known, warning, RiskDecision.NO_POSITION, RiskReason.INSUFFICIENT_REMAINING_CAPACITY)

        if final < base:
            reason = RiskReason.OPPOSING_MARKET_EXPOSURE if opposing else (RiskReason.HIGH_CORRELATION_EXPOSURE if correlated else RiskReason.CONFIRMED_EXPOSURE_PRESENT)
            decision = RiskDecision.REDUCED_STAKE
        else:
            reason = RiskReason.NO_CONFIRMED_EXPOSURE if not placed else RiskReason.CONFIRMED_EXPOSURE_PRESENT
            decision = RiskDecision.FULL_STAKE
        if not exposure_known and not placed and decision is RiskDecision.FULL_STAKE:
            reason = RiskReason.UNREGISTERED_EXPOSURE_WARNING

        return self._result(value, bankroll, unit_percent_value, unit_value, base, current, remaining, factor, final, exposure_known, warning, decision, reason)

    @staticmethod
    def _result(value: ValueAssessment, bankroll: float, unit_percent: float, unit_value: float, base: float, current: float, remaining: float, factor: float, final: float, exposure_known: bool, warning: str | None, decision: RiskDecision, reason: RiskReason) -> RiskAssessment:
        return RiskAssessment(
            match_id=value.match_id,
            as_of=value.as_of.astimezone(UTC),
            calculated_at=datetime.now(UTC),
            market=value.market,
            risk_engine_version=RISK_ENGINE_VERSION,
            value_engine_version=value.value_engine_version,
            market_engine_version=value.market_engine_version,
            model_version=value.model_version,
            feature_engine_version=value.feature_engine_version,
            value_decision=value.decision,
            bankroll_amount=bankroll,
            unit_percent=unit_percent,
            unit_value=unit_value,
            base_stake_units=base,
            current_match_exposure_units=current,
            max_match_exposure_units=MAX_MATCH_EXPOSURE_UNITS,
            remaining_match_capacity_units=remaining,
            correlation_adjustment=factor,
            final_stake_units=final,
            stake_amount=unit_value * final,
            exposure_known=exposure_known,
            exposure_warning=warning,
            risk_decision=decision,
            reason=reason,
        )
