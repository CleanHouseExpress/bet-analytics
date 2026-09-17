from __future__ import annotations

import logging
import math
from datetime import UTC, datetime
from time import perf_counter

from apps.api.app.domain.market_probability import (
    MARKET_ENGINE_VERSION,
    Market,
    MarketProbabilityReason,
    MarketProbabilityResult,
)
from apps.api.app.domain.poisson import (
    NUMERICAL_TOLERANCE,
    POISSON_MODEL_NAME,
    POISSON_MODEL_VERSION,
    PoissonResult,
)

logger = logging.getLogger(__name__)


class MarketProbabilityError(ValueError):
    def __init__(self, reason: MarketProbabilityReason):
        super().__init__(reason.value)
        self.reason = reason


def _positive_finite(value: float) -> bool:
    return math.isfinite(value) and value > 0.0


def _bounded_probability(value: float) -> float:
    if not math.isfinite(value):
        raise MarketProbabilityError(
            MarketProbabilityReason.INVALID_MARKET_PROBABILITY
        )
    if value < 0.0 and abs(value) <= NUMERICAL_TOLERANCE:
        return 0.0
    if value > 1.0 and abs(value - 1.0) <= NUMERICAL_TOLERANCE:
        return 1.0
    if not 0.0 <= value <= 1.0:
        raise MarketProbabilityError(
            MarketProbabilityReason.INVALID_MARKET_PROBABILITY
        )
    return value


def _poisson_cdf(lambda_: float, maximum: int) -> float:
    if not _positive_finite(lambda_):
        raise MarketProbabilityError(MarketProbabilityReason.INVALID_LAMBDA)
    if maximum < 0:
        return 0.0
    term = math.exp(-lambda_)
    probability = term
    for k in range(1, maximum + 1):
        term *= lambda_ / k
        probability += term
    return _bounded_probability(probability)


class MarketProbabilityEngine:
    def calculate_all(
        self,
        *,
        poisson: PoissonResult,
    ) -> tuple[MarketProbabilityResult, ...]:
        return tuple(
            self.calculate(poisson=poisson, market=market) for market in Market
        )

    def calculate(
        self,
        *,
        poisson: PoissonResult,
        market: Market | str,
    ) -> MarketProbabilityResult:
        started_at = perf_counter()
        try:
            result = self._calculate(poisson=poisson, market=market)
        except MarketProbabilityError as exc:
            as_of = getattr(poisson, "as_of", None)
            logger.warning(
                "market_probability_blocked",
                extra={
                    "match_id": getattr(poisson, "match_id", None),
                    "as_of": as_of.isoformat() if isinstance(as_of, datetime) else None,
                    "market": str(market),
                    "market_engine_version": MARKET_ENGINE_VERSION,
                    "model_version": getattr(poisson, "model_version", None),
                    "duration_ms": (perf_counter() - started_at) * 1000,
                    "reason": exc.reason.value,
                },
            )
            raise

        logger.info(
            "market_probability_calculated",
            extra={
                "match_id": result.match_id,
                "as_of": result.as_of.isoformat(),
                "market": result.market.value,
                "market_engine_version": result.market_engine_version,
                "model_version": result.model_version,
                "p_model": result.p_model,
                "fair_odds": result.fair_odds,
                "duration_ms": (perf_counter() - started_at) * 1000,
            },
        )
        return result

    def _calculate(
        self,
        *,
        poisson: PoissonResult,
        market: Market | str,
    ) -> MarketProbabilityResult:
        if not isinstance(poisson, PoissonResult):
            raise MarketProbabilityError(
                MarketProbabilityReason.INVALID_POISSON_RESULT
            )
        if (
            poisson.match_id <= 0
            or poisson.as_of.tzinfo is None
            or poisson.as_of.utcoffset() is None
        ):
            raise MarketProbabilityError(
                MarketProbabilityReason.INVALID_POISSON_RESULT
            )
        if (
            poisson.model_name != POISSON_MODEL_NAME
            or poisson.model_version != POISSON_MODEL_VERSION
        ):
            raise MarketProbabilityError(
                MarketProbabilityReason.INCOMPATIBLE_MODEL_VERSION
            )
        if not (
            _positive_finite(poisson.lambda_home)
            and _positive_finite(poisson.lambda_away)
        ):
            raise MarketProbabilityError(MarketProbabilityReason.INVALID_LAMBDA)
        if not (
            math.isfinite(poisson.matrix_probability_mass)
            and math.isfinite(poisson.tail_probability)
            and -NUMERICAL_TOLERANCE
            <= poisson.matrix_probability_mass
            <= 1.0 + NUMERICAL_TOLERANCE
            and -NUMERICAL_TOLERANCE
            <= poisson.tail_probability
            <= 1.0 + NUMERICAL_TOLERANCE
            and abs(
                poisson.matrix_probability_mass + poisson.tail_probability - 1.0
            )
            <= NUMERICAL_TOLERANCE
        ):
            raise MarketProbabilityError(
                MarketProbabilityReason.INVALID_PROBABILITY_MASS
            )

        try:
            market_enum = market if isinstance(market, Market) else Market(market)
        except (ValueError, TypeError):
            raise MarketProbabilityError(
                MarketProbabilityReason.UNSUPPORTED_MARKET
            ) from None

        lambda_total = poisson.lambda_home + poisson.lambda_away
        if not _positive_finite(lambda_total):
            raise MarketProbabilityError(MarketProbabilityReason.INVALID_LAMBDA)

        if market_enum is Market.TOTAL_GOALS_OVER_1_5:
            probability = 1.0 - _poisson_cdf(lambda_total, 1)
        elif market_enum is Market.TOTAL_GOALS_OVER_2_5:
            probability = 1.0 - _poisson_cdf(lambda_total, 2)
        elif market_enum is Market.TOTAL_GOALS_UNDER_3_5:
            probability = _poisson_cdf(lambda_total, 3)
        elif market_enum is Market.TOTAL_GOALS_UNDER_4_5:
            probability = _poisson_cdf(lambda_total, 4)
        else:
            home_zero = math.exp(-poisson.lambda_home)
            away_zero = math.exp(-poisson.lambda_away)
            both_zero = math.exp(-lambda_total)
            btts_yes = _bounded_probability(
                1.0 - home_zero - away_zero + both_zero
            )
            probability = (
                btts_yes if market_enum is Market.BTTS_YES else 1.0 - btts_yes
            )

        probability = _bounded_probability(probability)
        fair_odds = None if probability == 0.0 else 1.0 / probability
        if fair_odds is not None and not math.isfinite(fair_odds):
            raise MarketProbabilityError(
                MarketProbabilityReason.INVALID_MARKET_PROBABILITY
            )

        return MarketProbabilityResult(
            match_id=poisson.match_id,
            as_of=poisson.as_of.astimezone(UTC),
            calculated_at=datetime.now(UTC),
            market=market_enum,
            market_engine_version=MARKET_ENGINE_VERSION,
            model_name=poisson.model_name,
            model_version=poisson.model_version,
            feature_engine_version=poisson.feature_engine_version,
            p_model=probability,
            fair_odds=fair_odds,
        )
