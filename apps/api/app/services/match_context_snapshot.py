from __future__ import annotations

from dataclasses import asdict

from sqlalchemy import text
from sqlalchemy.orm import Session

from apps.api.app.domain.match_context import MatchContext


class MatchContextSnapshotConflict(ValueError):
    pass


def persist_match_context_snapshot(
    session: Session,
    *,
    evaluation_key: str,
    context: MatchContext,
) -> dict[str, object]:
    """Persist once per logical evaluation; an existing snapshot is immutable."""
    if not evaluation_key.strip():
        raise ValueError("evaluation_key is required")

    expected = asdict(context)
    expected["competition_format"] = context.competition_format.value
    expected["analysis_type"] = context.analysis_type.value

    sql = """
        INSERT INTO match_context_snapshots (
            evaluation_key, match_id, competition_id, season_id,
            competition_format, analysis_type, as_of, classified_at,
            classifier_version
        ) VALUES (
            :evaluation_key, :match_id, :competition_id, :season_id,
            :competition_format, :analysis_type, :as_of, :classified_at,
            :classifier_version
        )
    """
    if session.get_bind().dialect.name == "postgresql":
        sql += " ON CONFLICT (evaluation_key) DO NOTHING RETURNING evaluation_key"
    else:
        sql = sql.replace(
            "INSERT INTO match_context_snapshots",
            "INSERT OR IGNORE INTO match_context_snapshots",
            1,
        )
        sql += " RETURNING evaluation_key"

    inserted = session.execute(
        text(sql),
        {"evaluation_key": evaluation_key, **expected},
    ).scalar_one_or_none()
    session.flush()

    existing = session.execute(
        text(
            """
            SELECT evaluation_key, match_id, competition_id, season_id,
                   competition_format, analysis_type, as_of, classified_at,
                   classifier_version
              FROM match_context_snapshots
             WHERE evaluation_key = :evaluation_key
            """
        ),
        {"evaluation_key": evaluation_key},
    ).mappings().one()

    comparable = {key: existing[key] for key in expected}
    if comparable != expected:
        raise MatchContextSnapshotConflict(
            "evaluation_key already has a different immutable match context snapshot"
        )

    if inserted is None:
        return dict(existing)
    return {"evaluation_key": evaluation_key, **expected}
