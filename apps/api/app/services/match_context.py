from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.app.domain.history import Competition, Match, Season
from apps.api.app.domain.match_context import (
    AnalysisType,
    CompetitionFormat,
    MatchContext,
    MatchContextBlockReason,
    MatchContextClassification,
)

logger = logging.getLogger(__name__)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class MatchContextClassifier:
    """Fail-closed V1 classifier. Only league points + pre-match is supported."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def classify(
        self,
        match_id: int,
        *,
        as_of: datetime,
        analysis_type: AnalysisType = AnalysisType.PRE_MATCH,
    ) -> MatchContextClassification:
        classified_at = datetime.now(UTC)
        row = self.session.execute(
            select(Match, Competition, Season)
            .join(Competition, Match.competition_id == Competition.id)
            .join(Season, Match.season_id == Season.id)
            .where(Match.id == match_id)
        ).one_or_none()

        if row is None:
            return self._blocked(match_id, MatchContextBlockReason.INCOMPLETE_MATCH_CONTEXT, as_of)

        match, competition, season = row
        if season.competition_id != competition.id or match.competition_id != competition.id:
            return self._blocked(match_id, MatchContextBlockReason.INCOMPLETE_MATCH_CONTEXT, as_of)

        if analysis_type is not AnalysisType.PRE_MATCH:
            return self._blocked(match_id, MatchContextBlockReason.UNSUPPORTED_ANALYSIS_TYPE, as_of)

        # V1 deliberately relies on the canonical explicit competition_type. It does
        # not infer knockout/league semantics from names, rounds or team counts.
        if competition.competition_type.casefold() != "league":
            return self._blocked(match_id, MatchContextBlockReason.UNSUPPORTED_COMPETITION_FORMAT, as_of)

        as_of_utc = _utc(as_of)
        kickoff_utc = _utc(match.kickoff_at)
        if as_of_utc >= kickoff_utc:
            return self._blocked(match_id, MatchContextBlockReason.INVALID_AS_OF, as_of)

        context = MatchContext(
            match_id=match.id,
            competition_id=competition.id,
            season_id=season.id,
            competition_format=CompetitionFormat.LEAGUE_POINTS,
            analysis_type=AnalysisType.PRE_MATCH,
            classified_at=classified_at,
            as_of=as_of_utc,
        )
        logger.info(
            "match_context_classified",
            extra={
                "match_id": match.id,
                "competition_id": competition.id,
                "season_id": season.id,
                "competition_format": context.competition_format.value,
                "analysis_type": context.analysis_type.value,
                "as_of": context.as_of.isoformat(),
                "classifier_version": context.classifier_version,
            },
        )
        return MatchContextClassification(context=context)

    @staticmethod
    def _blocked(
        match_id: int,
        reason: MatchContextBlockReason,
        as_of: datetime,
    ) -> MatchContextClassification:
        logger.warning(
            "match_context_blocked",
            extra={
                "match_id": match_id,
                "as_of": _utc(as_of).isoformat(),
                "block_reason": reason.value,
            },
        )
        return MatchContextClassification(context=None, block_reason=reason)
