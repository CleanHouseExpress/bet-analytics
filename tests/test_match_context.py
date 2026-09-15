from datetime import UTC, datetime, timedelta

from apps.api.app.domain.match_context import (
    AnalysisType,
    CompetitionFormat,
    MatchContext,
    MatchContextBlockReason,
    check_thesis_compatibility,
)


def _context() -> MatchContext:
    now = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    return MatchContext(
        match_id=1,
        competition_id=1,
        season_id=1,
        competition_format=CompetitionFormat.LEAGUE_POINTS,
        analysis_type=AnalysisType.PRE_MATCH,
        classified_at=now,
        as_of=now - timedelta(hours=1),
    )


def test_thesis_accepts_compatible_match_context() -> None:
    result = check_thesis_compatibility(
        _context(),
        supported_formats={CompetitionFormat.LEAGUE_POINTS},
        supported_analysis_types={AnalysisType.PRE_MATCH},
    )
    assert result.compatible is True
    assert result.block_reason is None


def test_thesis_fails_closed_for_incompatible_context() -> None:
    result = check_thesis_compatibility(
        _context(),
        supported_formats={CompetitionFormat.KNOCKOUT_FIRST_LEG},
        supported_analysis_types={AnalysisType.PRE_MATCH},
    )
    assert result.compatible is False
    assert result.block_reason == MatchContextBlockReason.INCOMPATIBLE_MATCH_CONTEXT


def test_future_contract_values_do_not_imply_functional_support() -> None:
    assert CompetitionFormat.KNOCKOUT_FIRST_LEG.value == "KNOCKOUT_FIRST_LEG"
    assert CompetitionFormat.KNOCKOUT_SECOND_LEG.value == "KNOCKOUT_SECOND_LEG"
    assert AnalysisType.LIVE.value == "LIVE"
