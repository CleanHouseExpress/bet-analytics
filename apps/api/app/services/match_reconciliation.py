from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum


MATCH_KICKOFF_TOLERANCE = timedelta(hours=36)


class MatchReconciliationReason(StrEnum):
    NO_CANDIDATES = "NO_CANDIDATES"
    EXACT_KICKOFF = "EXACT_KICKOFF"
    UNIQUE_ROUND = "UNIQUE_ROUND"
    UNIQUE_STAGE = "UNIQUE_STAGE"
    NEAREST_KICKOFF = "NEAREST_KICKOFF"
    UNIQUE_PAIR = "UNIQUE_PAIR"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICTING_SINGLE_CANDIDATE = "CONFLICTING_SINGLE_CANDIDATE"


class MatchReconciliationError(ValueError):
    def __init__(
        self,
        reason: MatchReconciliationReason,
        *,
        candidate_ids: tuple[int, ...] = (),
    ) -> None:
        self.reason = reason
        self.candidate_ids = candidate_ids
        suffix = (
            f": candidates={','.join(str(value) for value in candidate_ids)}"
            if candidate_ids
            else ""
        )
        super().__init__(f"{reason.value}{suffix}")


@dataclass(frozen=True, slots=True)
class MatchCandidate:
    match_id: int
    kickoff_at: datetime
    round_number: int | None
    stage_name: str | None


@dataclass(frozen=True, slots=True)
class MatchReconciliation:
    match_id: int | None
    reason: MatchReconciliationReason
    candidate_ids: tuple[int, ...]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("MATCH_RECONCILIATION_REQUIRES_TIMEZONE_AWARE_KICKOFF")
    return value.astimezone(UTC)


def _normalize(value: str | None) -> str | None:
    if value is None:
        return None
    ascii_value = (
        unicodedata.normalize("NFKD", value)
        .encode("ascii", "ignore")
        .decode()
        .lower()
    )
    normalized = "".join(char for char in ascii_value if char.isalnum())
    return normalized or None


def _distance_seconds(candidate: MatchCandidate, kickoff_at: datetime) -> float:
    return abs((_utc(candidate.kickoff_at) - kickoff_at).total_seconds())


def resolve_match_candidate(
    *,
    candidates: tuple[MatchCandidate, ...],
    kickoff_at: datetime,
    round_number: int | None,
    stage_name: str | None,
    tolerance: timedelta = MATCH_KICKOFF_TOLERANCE,
) -> MatchReconciliation:
    kickoff_at = _utc(kickoff_at)
    if tolerance.total_seconds() < 0:
        raise ValueError("INVALID_MATCH_RECONCILIATION_TOLERANCE")

    candidate_ids = tuple(candidate.match_id for candidate in candidates)
    if not candidates:
        return MatchReconciliation(
            match_id=None,
            reason=MatchReconciliationReason.NO_CANDIDATES,
            candidate_ids=(),
        )

    exact_kickoff = tuple(
        candidate
        for candidate in candidates
        if _utc(candidate.kickoff_at) == kickoff_at
    )
    if len(exact_kickoff) == 1:
        return MatchReconciliation(
            match_id=exact_kickoff[0].match_id,
            reason=MatchReconciliationReason.EXACT_KICKOFF,
            candidate_ids=candidate_ids,
        )
    if len(exact_kickoff) > 1:
        raise MatchReconciliationError(
            MatchReconciliationReason.AMBIGUOUS,
            candidate_ids=tuple(candidate.match_id for candidate in exact_kickoff),
        )

    pool = candidates
    if round_number is not None:
        same_round = tuple(
            candidate
            for candidate in pool
            if candidate.round_number == round_number
        )
        if len(same_round) == 1:
            return MatchReconciliation(
                match_id=same_round[0].match_id,
                reason=MatchReconciliationReason.UNIQUE_ROUND,
                candidate_ids=candidate_ids,
            )
        if same_round:
            pool = same_round

    normalized_stage = _normalize(stage_name)
    if normalized_stage is not None:
        same_stage = tuple(
            candidate
            for candidate in pool
            if _normalize(candidate.stage_name) == normalized_stage
        )
        if len(same_stage) == 1:
            return MatchReconciliation(
                match_id=same_stage[0].match_id,
                reason=MatchReconciliationReason.UNIQUE_STAGE,
                candidate_ids=candidate_ids,
            )
        if same_stage:
            pool = same_stage

    close = tuple(
        candidate
        for candidate in pool
        if _distance_seconds(candidate, kickoff_at) <= tolerance.total_seconds()
    )
    if len(close) == 1:
        return MatchReconciliation(
            match_id=close[0].match_id,
            reason=MatchReconciliationReason.NEAREST_KICKOFF,
            candidate_ids=candidate_ids,
        )
    if len(close) > 1:
        raise MatchReconciliationError(
            MatchReconciliationReason.AMBIGUOUS,
            candidate_ids=tuple(candidate.match_id for candidate in close),
        )

    if len(candidates) == 1:
        candidate = candidates[0]
        round_conflict = (
            round_number is not None
            and candidate.round_number is not None
            and candidate.round_number != round_number
        )
        candidate_stage = _normalize(candidate.stage_name)
        stage_conflict = (
            normalized_stage is not None
            and candidate_stage is not None
            and candidate_stage != normalized_stage
        )
        if round_conflict or stage_conflict:
            raise MatchReconciliationError(
                MatchReconciliationReason.CONFLICTING_SINGLE_CANDIDATE,
                candidate_ids=candidate_ids,
            )
        return MatchReconciliation(
            match_id=candidate.match_id,
            reason=MatchReconciliationReason.UNIQUE_PAIR,
            candidate_ids=candidate_ids,
        )

    raise MatchReconciliationError(
        MatchReconciliationReason.AMBIGUOUS,
        candidate_ids=candidate_ids,
    )
