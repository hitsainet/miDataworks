"""The diversity report's method version 2: the split-half calibration positive control.

Decision 2026-10-19 (FPRD 007 FR-007.41, FTDD 007 §6.6). Method version 1's positive control asked
ONE random split of the reference not to "fall". A correctly calibrated figure does that on about
0.3% of splits (two 95% intervals miss each other when the halves differ by z > 2.77), so on the
integration fixture 8 in 2,000 report seeds read ``invalid`` on noise alone (mirror CI on
``a055a38``). Version 2 measures the halves' standardised differences over eight splits and fails
a figure only when they exceed what its own intervals allow (chi-square bound, 0.001 per report).

The fixture is the integration test's reference (``test_diversity_report.lineage``), built the way
the report builds it: completions ``sentences(300, 2)``, the fake endpoint's hashed-bag embeddings,
004's lexical clusters (k = 8) fitted with the report seed. ``V1_FAILING_SEEDS`` are the report
seeds on which method version 1 failed its positive control (found by a sweep of seeds 0-11,999
through version 1's code, 2026-10-19); version 2's first split is version 1's split, so the test
proves they are still adversarial (``first_split_falls``) instead of trusting the list.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Callable
from functools import cache, lru_cache
from typing import Any

import numpy as np
import pytest

from src.services.curation.cluster_service import assign_matrix, fit
from src.services.generation import diversity_metrics as dm
from src.services.generation import diversity_service as ds

TOPICS = [[f"t{t}w{i}" for i in range(80)] for t in range(8)]
RESAMPLES = 200  # the integration fixture's DIVERSITY_BOOTSTRAP_RESAMPLES

#: Report seeds on which method version 1's positive control failed on this fixture: the first 12
#: of 67 in seeds 0-11,999 (version 2 failed 5 of the 12,000, none of these).
V1_FAILING_SEEDS = (10, 109, 274, 401, 893, 961, 988, 1007, 1042, 1412, 1421, 1450)
#: Seeds where a half lacks a small cluster, so coverage differs by one cluster with a zero-width
#: interval: version 2 WITHOUT the one-step correction failed them on coverage (10 of its 11
#: failures in seeds 0-1,999).
COVERAGE_STEP_SEEDS = (303, 457, 691, 1241)
#: An unselected run of seeds, so the bound is not only checked on adversarial ones.
PLAIN_SEEDS = tuple(range(40))


def sentences(n: int, seed: int) -> list[str]:
    rng = random.Random(seed)  # noqa: S311 - a test corpus
    return [" ".join(rng.choice(TOPICS[i % 8]) for _ in range(14)) for i in range(n)]


def fake_embeddings(texts: list[str]) -> np.ndarray:
    """``tests/support/fake_millm.py``'s ``/v1/embeddings``: a bag of hashed words, 16 dims."""
    out = np.zeros((len(texts), 16))
    for i, text in enumerate(texts):
        for word in text.lower().split():
            out[i, hashlib.sha256(word.encode()).digest()[0] % 16] += 1.0
    return out


@lru_cache(maxsize=1)
def reference() -> tuple[list[str], np.ndarray, list[str]]:
    prompts, completions = sentences(300, 1), sentences(300, 2)
    cluster_texts = [f"{c}\n{p}" for p, c in zip(prompts, completions, strict=True)]
    return completions, fake_embeddings(completions), cluster_texts


def labels_for(cluster_texts: list[str], seed: int) -> tuple[list[int], int]:
    fitted = fit(cluster_texts, 8, seed)
    labels = assign_matrix(cluster_texts, fitted.idf, fitted.centroids)[0]
    return [int(x) for x in labels], int(fitted.k)


def fresh_checks(seed: int) -> dict[str, dict[str, Any]]:
    texts, vectors, cluster_texts = reference()
    labels, k = labels_for(cluster_texts, seed)
    checks = ds.controls_for(texts, vectors, labels, k, seed, RESAMPLES)
    return {c["check_id"]: c for c in checks}


@cache
def checks_for(seed: int) -> dict[str, dict[str, Any]]:
    """Cached across this file's tests (they only read it); a monkeypatched test calls
    :func:`fresh_checks`."""
    return fresh_checks(seed)


# --- pure parts ------------------------------------------------------------------------------


def test_the_chi_square_tail_matches_known_values() -> None:
    assert dm.chi2_sf_even(3.0, 2) == pytest.approx(math.exp(-1.5))
    assert dm.chi2_isf_even(0.05, 2) == pytest.approx(5.991465, abs=1e-5)
    assert dm.chi2_isf_even(0.001, 8) == pytest.approx(26.124482, abs=1e-5)
    assert dm.chi2_isf_even(0.05, 8) == pytest.approx(15.507313, abs=1e-5)
    with pytest.raises(ValueError):
        dm.chi2_sf_even(1.0, 3)


def test_the_bound_shares_alpha_across_the_measured_figures() -> None:
    five = dm.calibration_bound(5, 8, 0.001)
    assert five == pytest.approx(dm.chi2_isf_even(0.0002, 8) / 8)
    assert dm.calibration_bound(4, 8, 0.001) < five  # fewer figures, less to share


def test_standardised_difference_reads_the_intervals() -> None:
    a = dm.Interval(1.0, 1.0 - 1.96, 1.0 + 1.96)  # se 1
    b = dm.Interval(0.0, -1.96, 1.96)
    assert dm.standardised_difference(a, b) == pytest.approx(1 / math.sqrt(2), rel=1e-3)
    assert dm.standardised_difference(dm.Interval(None, None, None), b) is None
    flat = dm.Interval(0.5, 0.5, 0.5)
    assert dm.standardised_difference(flat, flat) == 0.0
    assert dm.standardised_difference(flat, dm.Interval(0.4, 0.4, 0.4)) == math.inf


def test_one_step_of_a_lattice_figure_is_not_a_difference() -> None:
    """Coverage moves in whole clusters: a half without one small cluster reads 7/8 in every
    subsample (zero width) against 8/8. One step is the figure's resolution, not evidence."""
    seven = dm.Interval(0.875, 0.875, 0.875)
    eight = dm.Interval(1.0, 0.875, 1.0)
    assert dm.standardised_difference(seven, eight, 0.125) == 0.0
    assert dm.standardised_difference(seven, eight) == pytest.approx(-0.125 / (0.125 / 3.919928))
    two_steps = dm.Interval(0.75, 0.75, 0.75)
    assert dm.standardised_difference(two_steps, eight, 0.125) == pytest.approx(
        -0.125 / (0.125 / 3.919928)
    )
    assert dm.standardised_difference(two_steps, dm.Interval(1.0, 1.0, 1.0), 0.125) == -math.inf


def test_a_figure_fails_only_past_the_bound() -> None:
    bound = dm.calibration_bound(2, 8, 0.001)
    calm = [1.0, -1.0] * 4  # mean square 1
    wide = [math.sqrt(bound) * 1.01] * 8
    failing, stats, got = dm.split_half_calibration({"a": calm, "b": wide}, 0.001)
    assert got == pytest.approx(bound) and failing == ["b"]
    assert stats["a"] == pytest.approx(1.0)
    # an unmeasured figure is not a figure: it neither fails nor takes a share of alpha
    failing, stats, got = dm.split_half_calibration({"a": calm, "spread": [None] * 8}, 0.001)
    assert failing == [] and "spread" not in stats
    assert got == pytest.approx(dm.calibration_bound(1, 8, 0.001))


def test_spread_ci_is_unchanged_by_normalising_once() -> None:
    """``spread_ci`` normalises the rows once instead of per resample (a speed-up that pays for
    the eight control splits); its interval must be the one ``centroid_spread`` per resample
    gives, or the figures would silently change under method version 2."""
    rng = np.random.default_rng(3)
    vectors = rng.normal(size=(120, 24)) + 0.4
    vectors[5] = 0.0  # a zero row is kept as zero by both
    got = dm.spread_ci(vectors, np.random.default_rng(11), 300, 60)

    def stat(idx: np.ndarray) -> float:
        value = dm.centroid_spread(vectors[idx])
        return float("nan") if value is None else value

    want = dm.bootstrap_ci(stat, vectors.shape[0], np.random.default_rng(11), 300, 60)
    assert got.as_dict() == pytest.approx(want.as_dict(), rel=1e-12, abs=1e-15)


# --- the false-failure rate and the power, on the fixture ------------------------------------


def test_the_seed_set_is_adversarial_for_method_version_1() -> None:
    """Each listed seed's first split is version 1's split, and it falls: version 1 failed there."""
    assert len(V1_FAILING_SEEDS) >= 8
    for seed in V1_FAILING_SEEDS:
        statistic = checks_for(seed)["diversity_positive_control"]["statistic"]
        assert statistic["first_split_falls"], seed


def test_the_positive_control_false_failure_rate_is_bounded() -> None:
    """Version 2 fails at most one of the seeds version 1 failed on, plus an unselected run.

    Nominal rate 0.001 per report. Measured (2026-10-19, through this code): 5 in 12,000 fixture
    seeds (0.042%, all on spread at the chi-square tail); version 1 failed 67 (0.56%). Reverting the control to version 1's single-split rule
    turns every ``V1_FAILING_SEEDS`` entry red here (recorded negative control)."""
    failed = []
    for seed in (*V1_FAILING_SEEDS, *COVERAGE_STEP_SEEDS, *PLAIN_SEEDS):
        check = checks_for(seed)["diversity_positive_control"]
        assert check["version"] == 2 and check["statistic"]["splits"] == dm.POSITIVE_CONTROL_SPLITS
        if check["result"] == "fail":
            failed.append((seed, check["statistic"]["exceeds"]))
    assert len(failed) <= 1, failed


def test_one_small_cluster_missing_from_a_half_is_not_read_as_noise() -> None:
    for seed in COVERAGE_STEP_SEEDS:
        statistic = checks_for(seed)["diversity_positive_control"]["statistic"]
        coverage = statistic["mean_square_z"]["cluster_coverage"]
        assert coverage != "infinite" and coverage <= statistic["bound"], (seed, statistic)


def test_the_negative_control_still_sees_a_collapse() -> None:
    misses = [
        s for s in PLAIN_SEEDS if checks_for(s)["diversity_negative_control"]["result"] != "pass"
    ]
    assert misses == []


def _shrunk(factor: float) -> Callable[..., dm.Interval]:
    original = dm.bootstrap_ci

    def shrunk(*args: Any, **kwargs: Any) -> dm.Interval:
        got = original(*args, **kwargs)
        if got.value is None or got.lo is None or got.hi is None:
            return got
        return dm.Interval(
            got.value,
            got.value - factor * (got.value - got.lo),
            got.value + factor * (got.hi - got.value),
        )

    return shrunk


def test_the_positive_control_still_fails_intervals_that_understate_the_noise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Power: intervals a third of their honest width (the halves then differ by ~3x what the
    intervals claim). Version 2 must call that out on nearly every report; version 1 did on 10%
    at half width (2,000-seed simulation, 2026-10-19)."""
    monkeypatch.setattr(dm, "bootstrap_ci", _shrunk(1 / 3))
    seeds = PLAIN_SEEDS[:20]
    failed = [s for s in seeds if fresh_checks(s)["diversity_positive_control"]["result"] == "fail"]
    assert len(failed) >= 19, failed


def test_a_planted_narrowing_still_falls() -> None:
    """The integration fixture's narrowing (600 rows of three near-copies) falls with both
    controls passing: the report's verdict is ``falls``, not ``invalid``."""
    narrow = [
        "sure thing here is the same answer again",
        "sure thing here is the same answer",
        "sure here is the same answer again",
    ]
    texts, vectors, cluster_texts = reference()
    generated = [narrow[i % 3] for i in range(600)]
    prompts = sentences(600, 9)
    child = texts + generated
    child_clusters = cluster_texts + [f"{c}\n{p}" for p, c in zip(prompts, generated, strict=True)]
    for seed in PLAIN_SEEDS[:10]:
        fitted = fit(cluster_texts, 8, seed)
        r_lab = assign_matrix(cluster_texts, fitted.idf, fitted.centroids)[0]
        c_lab = assign_matrix(child_clusters, fitted.idf, fitted.centroids)[0]
        figures = ds.figures_for(
            child, texts, fake_embeddings(child), vectors, c_lab, r_lab, fitted.k, seed, RESAMPLES
        )
        checks = ds.controls_for(texts, vectors, list(r_lab), fitted.k, seed, RESAMPLES)
        assert ds.verdict_of(figures, checks) == "falls", (seed, checks)
        assert figures["distinct_2"]["verdict"] == "falls"


def test_every_report_records_its_method_version() -> None:
    method = ds.method_for("completion", ["train"], 300, 7, RESAMPLES, 8, None)
    # 3 since the largest share's refit (proposed 2026-10-19); the positive control is still v2's
    assert method["method_version"] == ds.METHOD_VERSION == 3
    assert method["positive_control"] == {
        "method": "split_half_calibration_v2",
        "splits": 8,
        "alpha": 0.001,
    }
    # a report written before the field existed reads as version 1
    assert ds.method_version_of({"interval": "half_subsample_percentile_v1"}) == 1
    assert ds.method_version_of(None) == 1
    assert ds.method_version_of(method) == 3


def test_the_registry_describes_the_controls_by_their_own_ids() -> None:
    """Pre-existing defect fixed 2026-10-19: one function served both controls and answered as
    the positive one for every gate-3 metric id."""
    from src.services.calibration import checks as calibration_checks
    from src.services.generation import metric_registration

    metric_registration.register()
    for check_id, version in ((metric_registration.NEGATIVE, 1), (metric_registration.POSITIVE, 2)):
        spec = calibration_checks.CHECKS[check_id]
        result = spec.fn(None, "distinct_2")  # type: ignore[arg-type]
        assert (result.check_id, result.version, spec.version) == (check_id, version, version)


def test_too_few_reference_rows_fail_both_controls_at_their_versions() -> None:
    checks = ds.controls_for(["a b", "c d", "e f"], None, [0, 0, 1], 2, 1, RESAMPLES)
    assert [(c["check_id"], c["version"], c["result"]) for c in checks] == [
        ("diversity_negative_control", 1, "fail"),
        ("diversity_positive_control", 2, "fail"),
    ]
