from datetime import datetime, timezone

from apps.api.app.domain.features import (
    CompetitionBaseline,
    FeatureSet,
    FormWindow,
    StrengthFeatures,
)
from apps.api.app.services.feature_snapshot import semantic_hash, semantic_payload


def make_features(calculated_at: datetime) -> FeatureSet:
    empty = FormWindow(0, None, None, None, 0, 0, 0, False)
    return FeatureSet(
        match_id=1,
        as_of=datetime(2026, 9, 15, 12, tzinfo=timezone.utc),
        calculated_at=calculated_at,
        context_classifier_version="match-context-v1",
        feature_engine_version="feature-engine-v1",
        home_last5=empty,
        home_last10=empty,
        away_last5=empty,
        away_last10=empty,
        home_home5=empty,
        home_home10=empty,
        away_away5=empty,
        away_away10=empty,
        competition_baseline=CompetitionBaseline(0, None, None, None),
        strengths=StrengthFeatures(None, None, None, None),
        reasons=(),
    )


def test_calculated_at_is_not_part_of_semantic_snapshot():
    first = make_features(datetime(2026, 9, 15, 12, 1, tzinfo=timezone.utc))
    second = make_features(datetime(2026, 9, 15, 12, 2, tzinfo=timezone.utc))
    assert semantic_payload(first) == semantic_payload(second)
    assert semantic_hash(first) == semantic_hash(second)


def test_semantic_change_changes_hash():
    first = make_features(datetime(2026, 9, 15, 12, 1, tzinfo=timezone.utc))
    second = make_features(datetime(2026, 9, 15, 12, 1, tzinfo=timezone.utc))
    object.__setattr__(second, "match_id", 2)
    assert semantic_hash(first) != semantic_hash(second)
