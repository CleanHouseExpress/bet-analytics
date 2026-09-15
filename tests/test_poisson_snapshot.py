from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from apps.api.app.core.database import SessionLocal
from apps.api.app.domain.features import (
    CompetitionBaseline,
    FeatureSet,
    FormWindow,
    StrengthFeatures,
)
from apps.api.app.services.poisson_model import PoissonModel
from apps.api.app.services.poisson_snapshot import (
    PoissonSnapshotConflict,
    persist_poisson_snapshot,
    semantic_hash,
    semantic_payload,
)


def make_result(*, match_id: int):
    form = FormWindow(10, 1.0, 1.0, 1.5, 4, 3, 3, True)

    features = FeatureSet(
        match_id=match_id,
        as_of=datetime(2099, 9, 15, 16, tzinfo=timezone.utc),
        calculated_at=datetime(2099, 9, 15, 16, 1, tzinfo=timezone.utc),
        context_classifier_version="match-context-v1",
        feature_engine_version="feature-engine-v1",
        home_last5=form,
        home_last10=form,
        away_last5=form,
        away_last10=form,
        home_home5=form,
        home_home10=form,
        away_away5=form,
        away_away10=form,
        competition_baseline=CompetitionBaseline(
            240,
            1.5375,
            1.1375,
            2.675,
        ),
        strengths=StrengthFeatures(
            0.975609756097561,
            1.3186813186813187,
            0.43956043956043955,
            0.9105691056910568,
        ),
        reasons=(),
    )

    return PoissonModel().calculate(features=features)


def seed_target_match(session) -> int:
    now = datetime(2099, 1, 1, tzinfo=timezone.utc)

    comp = session.execute(
        text(
            """
            INSERT INTO competitions (
                name, country_code, competition_type, created_at, updated_at
            )
            VALUES (
                'BETS-4 Snapshot Test League',
                'BRA',
                'league',
                :now,
                :now
            )
            RETURNING id
            """
        ),
        {"now": now},
    ).scalar_one()

    season = session.execute(
        text(
            """
            INSERT INTO seasons (
                competition_id, name, is_current, created_at, updated_at
            )
            VALUES (
                :competition_id,
                '2099',
                false,
                :now,
                :now
            )
            RETURNING id
            """
        ),
        {"competition_id": comp, "now": now},
    ).scalar_one()

    home = session.execute(
        text(
            """
            INSERT INTO teams (
                name, country_code, created_at, updated_at
            )
            VALUES (
                'BETS-4 Snapshot Home',
                'BRA',
                :now,
                :now
            )
            RETURNING id
            """
        ),
        {"now": now},
    ).scalar_one()

    away = session.execute(
        text(
            """
            INSERT INTO teams (
                name, country_code, created_at, updated_at
            )
            VALUES (
                'BETS-4 Snapshot Away',
                'BRA',
                :now,
                :now
            )
            RETURNING id
            """
        ),
        {"now": now},
    ).scalar_one()

    match_id = session.execute(
        text(
            """
            INSERT INTO matches (
                competition_id,
                season_id,
                home_team_id,
                away_team_id,
                kickoff_at,
                status,
                created_at,
                updated_at
            )
            VALUES (
                :competition_id,
                :season_id,
                :home_team_id,
                :away_team_id,
                :kickoff_at,
                'scheduled',
                :now,
                :now
            )
            RETURNING id
            """
        ),
        {
            "competition_id": comp,
            "season_id": season,
            "home_team_id": home,
            "away_team_id": away,
            "kickoff_at": datetime(
                2099, 9, 16, 22, 30, tzinfo=timezone.utc
            ),
            "now": now,
        },
    ).scalar_one()

    return match_id


def test_calculated_at_is_not_semantic():
    first = make_result(match_id=1)
    second = replace(
        first,
        calculated_at=datetime(2100, 1, 1, tzinfo=timezone.utc),
    )

    assert semantic_payload(first) == semantic_payload(second)
    assert semantic_hash(first) == semantic_hash(second)


def test_semantic_change_changes_hash():
    first = make_result(match_id=1)
    second = replace(
        first,
        lambda_home=first.lambda_home + 0.01,
    )

    assert semantic_hash(first) != semantic_hash(second)


def test_semantic_hash_is_deterministic():
    result = make_result(match_id=1)

    assert semantic_hash(result) == semantic_hash(result)


def test_persistence_is_idempotent():
    with SessionLocal() as session:
        match_id = seed_target_match(session)
        result = make_result(match_id=match_id)
        key = f"BETS-4-test-idempotent-{match_id}"

        first = persist_poisson_snapshot(
            session,
            evaluation_key=key,
            result=result,
        )

        second = persist_poisson_snapshot(
            session,
            evaluation_key=key,
            result=replace(
                result,
                calculated_at=datetime(
                    2100, 1, 1, tzinfo=timezone.utc
                ),
            ),
        )

        count = session.execute(
            text(
                """
                SELECT count(*)
                FROM model_snapshots
                WHERE evaluation_key = :key
                """
            ),
            {"key": key},
        ).scalar_one()

        assert count == 1
        assert first["semantic_hash"] == second["semantic_hash"]

        session.rollback()


def test_same_key_with_different_semantics_conflicts():
    with SessionLocal() as session:
        match_id = seed_target_match(session)
        result = make_result(match_id=match_id)
        key = f"BETS-4-test-conflict-{match_id}"

        persist_poisson_snapshot(
            session,
            evaluation_key=key,
            result=result,
        )

        changed = replace(
            result,
            lambda_home=result.lambda_home + 0.01,
        )

        with pytest.raises(PoissonSnapshotConflict):
            persist_poisson_snapshot(
                session,
                evaluation_key=key,
                result=changed,
            )

        session.rollback()
