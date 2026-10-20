"""The metric registry (006 FTASKS 4.1, 4.2): walked LIVE, never a copied list."""

from __future__ import annotations

import pytest

from src.services.calibration import registry as r
from src.services.calibration.checks import CHECKS


def test_every_gate_metric_names_checks_that_exist() -> None:
    gate = [s for s in r.METRIC_REGISTRY.values() if s.gate is not None]
    assert gate, "no gate metric is registered"
    for spec in gate:
        assert spec.checks, spec.metric_id
        assert set(spec.checks) <= set(CHECKS), spec.metric_id


def test_the_version_one_gate_two_entries() -> None:
    assert r.gate_metric_ids("gate2") == {"auroc", "ceiling", "comparison_auroc"}
    assert r.invalidating_metric_ids("gate2") == {"auroc"}
    assert set(r.METRIC_REGISTRY["auroc"].checks) == {
        "both_classes",
        "label_permutation_null",
        "row_alignment",
        "shortcut_comparison",
    }
    assert r.METRIC_REGISTRY["paired"].gate is None
    assert r.METRIC_REGISTRY["reference_diagnostic"].gate is None


def test_a_gate_metric_without_checks_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one sanity check"):
        r.register_metric(r.MetricSpec("diversity_x", "gate3", ()))
    assert "diversity_x" not in r.METRIC_REGISTRY


def test_an_unknown_check_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown check"):
        r.register_metric(r.MetricSpec("diversity_y", "gate3", ("no_such_check",)))


def test_another_feature_registers_a_gate_three_figure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A spec registered by another module (standing in for 007) appears in gate3."""
    monkeypatch.setattr(r, "METRIC_REGISTRY", dict(r.METRIC_REGISTRY))
    r.register_metric(r.MetricSpec("distinct_ngrams", "gate3", ("both_classes",)))
    # 007's own gate-3 metrics may already be registered (feature 007, FTASKS 9.7).
    assert "distinct_ngrams" in [s.metric_id for s in r.gate_metrics("gate3")]
    assert "distinct_ngrams" not in r.gate_metric_ids("gate2")
