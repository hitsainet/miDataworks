"""The feature filter's top-k rule (FR-009.66): a feature missing from a position's recorded top
features is below the cutoff ONLY when that position's smallest recorded value is; otherwise its
activation is unknown and never read as zero (control P22's regression test)."""

from __future__ import annotations

from src.operators.native.detector.feature_filter import row_activation


def pos(*pairs: tuple[int, float]) -> dict:
    return {"position": 0, "token_id": 1, "features": [{"index": i, "value": v} for i, v in pairs]}


def test_a_missing_feature_below_a_low_kth_value_is_known_below() -> None:
    activation, feature, known = row_activation([pos((1, 5.0), (2, 0.5))], [9], cutoff=1.0)
    assert (activation, feature, known) == (None, None, True)


def test_a_missing_feature_under_top_values_that_all_clear_the_cutoff_is_unknown() -> None:
    activation, feature, known = row_activation([pos((1, 5.0), (2, 3.0))], [9], cutoff=1.0)
    assert activation is None and known is False


def test_a_present_feature_reports_its_largest_value() -> None:
    activation, feature, known = row_activation(
        [pos((9, 2.0), (1, 1.5)), pos((9, 4.0), (1, 0.1))], [9], cutoff=1.0
    )
    assert (activation, feature, known) == (4.0, 9, True)
