from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


CLASSIFIER_VERSION = "match-context-v1"


class CompetitionFormat(StrEnum):
    LEAGUE_POINTS = "LEAGUE_POINTS"
    KNOCKOUT_FIRST_LEG = "KNOCKOUT_FIRST_LEG"
    KNOCKOUT_SECOND_LEG = "KNOCKOUT_SECOND_LEG"


class AnalysisType(StrEnum):
    PRE_MATCH = "PRE_MATCH"
    LIVE = "LIVE"


class MatchContextBlockReason(StrEnum):
    INCOMPLETE_MATCH_CONTEXT = "INCOMPLETE_MATCH_CONTEXT"
    UNSUPPORTED_COMPETITION_FORMAT = "UNSUPPORTED_COMPETITION_FORMAT"
    UNSUPPORTED_ANALYSIS_TYPE = "UNSUPPORTED_ANALYSIS_TYPE"
    INVALID_AS_OF = "INVALID_AS_OF"
    INCOMPATIBLE_MATCH_CONTEXT = "INCOMPATIBLE_MATCH_CONTEXT"


@dataclass(frozen=True, slots=True)
class MatchContext:
    match_id: int
    competition_id: int
    season_id: int
    competition_format: CompetitionFormat
    analysis_type: AnalysisType
    classified_at: datetime
    as_of: datetime
    classifier_version: str = CLASSIFIER_VERSION


@dataclass(frozen=True, slots=True)
class MatchContextClassification:
    context: MatchContext | None
    block_reason: MatchContextBlockReason | None = None

    @property
    def supported(self) -> bool:
        return self.context is not None and self.block_reason is None


@dataclass(frozen=True, slots=True)
class ThesisCompatibility:
    compatible: bool
    block_reason: MatchContextBlockReason | None = None


def check_thesis_compatibility(
    context: MatchContext,
    *,
    supported_formats: set[CompetitionFormat] | frozenset[CompetitionFormat],
    supported_analysis_types: set[AnalysisType] | frozenset[AnalysisType],
) -> ThesisCompatibility:
    if (
        context.competition_format not in supported_formats
        or context.analysis_type not in supported_analysis_types
    ):
        return ThesisCompatibility(
            compatible=False,
            block_reason=MatchContextBlockReason.INCOMPATIBLE_MATCH_CONTEXT,
        )
    return ThesisCompatibility(compatible=True)
