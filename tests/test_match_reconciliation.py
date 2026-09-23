from datetime import UTC, datetime, timedelta

import pytest

from apps.api.app.services.match_reconciliation import (
    MATCH_KICKOFF_TOLERANCE,
    MatchCandidate,
    MatchReconciliationError,
    MatchReconciliationReason,
    resolve_match_candidate,
)


BASE = datetime(2026, 9, 23, 18, 0, tzinfo=UTC)


def candidate(
    match_id: int,
    *,
    hours: int = 0,
    round_number: int | None = None,
    stage_name: str | None = None,
) -> MatchCandidate:
    return MatchCandidate(
        match_id=match_id,
        kickoff_at=BASE + timedelta(hours=hours),
        round_number=round_number,
        stage_name=stage_name,
    )


def test_no_candidates_returns_create_resolution():
    result = resolve_match_candidate(
        candidates=(),
        kickoff_at=BASE,
        round_number=1,
        stage_name="Regular Season",
    )
    assert result.match_id is None
    assert result.reason is MatchReconciliationReason.NO_CANDIDATES


def test_exact_kickoff_disambiguates_repeated_fixture():
    result = resolve_match_candidate(
        candidates=(
            candidate(10, hours=-240, round_number=1),
            candidate(20, round_number=20),
        ),
        kickoff_at=BASE,
        round_number=None,
        stage_name=None,
    )
    assert result.match_id == 20
    assert result.reason is MatchReconciliationReason.EXACT_KICKOFF


def test_unique_round_survives_large_postponement():
    result = resolve_match_candidate(
        candidates=(
            candidate(10, hours=-500, round_number=1),
            candidate(20, hours=-300, round_number=20),
        ),
        kickoff_at=BASE,
        round_number=20,
        stage_name=None,
    )
    assert result.match_id == 20
    assert result.reason is MatchReconciliationReason.UNIQUE_ROUND


def test_unique_stage_disambiguates_repeated_fixture():
    result = resolve_match_candidate(
        candidates=(
            candidate(10, hours=-500, stage_name="Group Stage"),
            candidate(20, hours=-300, stage_name="Final"),
        ),
        kickoff_at=BASE,
        round_number=None,
        stage_name="FINAL",
    )
    assert result.match_id == 20
    assert result.reason is MatchReconciliationReason.UNIQUE_STAGE


def test_conflicting_round_and_stage_evidence_blocks():
    with pytest.raises(
        MatchReconciliationError,
        match=MatchReconciliationReason.AMBIGUOUS.value,
    ):
        resolve_match_candidate(
            candidates=(
                candidate(
                    10,
                    hours=-240,
                    round_number=2,
                    stage_name="Group Stage",
                ),
                candidate(
                    20,
                    hours=-480,
                    round_number=1,
                    stage_name="Final",
                ),
            ),
            kickoff_at=BASE,
            round_number=1,
            stage_name="Group Stage",
        )


def test_single_candidate_inside_tolerance_reconciles():
    result = resolve_match_candidate(
        candidates=(candidate(10, hours=-12),),
        kickoff_at=BASE,
        round_number=None,
        stage_name=None,
    )
    assert result.match_id == 10
    assert result.reason is MatchReconciliationReason.NEAREST_KICKOFF


def test_unique_pair_preserves_legacy_reschedule_fallback_without_conflict():
    result = resolve_match_candidate(
        candidates=(candidate(10, hours=-240),),
        kickoff_at=BASE,
        round_number=None,
        stage_name=None,
    )
    assert result.match_id == 10
    assert result.reason is MatchReconciliationReason.UNIQUE_PAIR


def test_single_candidate_with_conflicting_round_fails_closed():
    with pytest.raises(
        MatchReconciliationError,
        match=MatchReconciliationReason.CONFLICTING_SINGLE_CANDIDATE.value,
    ):
        resolve_match_candidate(
            candidates=(candidate(10, hours=-240, round_number=1),),
            kickoff_at=BASE,
            round_number=2,
            stage_name=None,
        )


def test_multiple_close_candidates_fail_closed_instead_of_guessing():
    with pytest.raises(
        MatchReconciliationError,
        match=MatchReconciliationReason.AMBIGUOUS.value,
    ):
        resolve_match_candidate(
            candidates=(
                candidate(10, hours=-6),
                candidate(20, hours=6),
            ),
            kickoff_at=BASE,
            round_number=None,
            stage_name=None,
        )


def test_multiple_unresolved_repeated_fixtures_fail_closed():
    with pytest.raises(
        MatchReconciliationError,
        match=MatchReconciliationReason.AMBIGUOUS.value,
    ):
        resolve_match_candidate(
            candidates=(
                candidate(10, hours=-500, round_number=1),
                candidate(20, hours=500, round_number=2),
            ),
            kickoff_at=BASE,
            round_number=None,
            stage_name=None,
        )


def test_negative_tolerance_is_rejected():
    with pytest.raises(ValueError, match="INVALID_MATCH_RECONCILIATION_TOLERANCE"):
        resolve_match_candidate(
            candidates=(candidate(10),),
            kickoff_at=BASE,
            round_number=None,
            stage_name=None,
            tolerance=-MATCH_KICKOFF_TOLERANCE,
        )
