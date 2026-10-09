"""Pure decision rules (005 FTASKS 3.1 – 3.7). Fixtures sit ON the boundaries and one ulp either
side: a fixture that avoids the boundary cannot catch ``>=`` becoming ``>``."""

from __future__ import annotations

import math

import pytest

from src.services import labeling_rules as r

POS, NEG = 0.50, 0.20


class TestDecideBinary:
    @pytest.mark.parametrize(
        ("p", "expected"),
        [
            (0.50, "positive"),
            (math.nextafter(0.50, 1.0), "positive"),
            (math.nextafter(0.50, 0.0), "excluded"),
            (0.20, "negative"),
            (math.nextafter(0.20, 0.0), "negative"),
            (math.nextafter(0.20, 1.0), "excluded"),
            (0.0, "negative"),
            (1.0, "positive"),
            (0.35, "excluded"),
        ],
    )
    def test_inclusive_at_both_thresholds(self, p: float, expected: str) -> None:
        assert r.decide_binary(p, POS, NEG) == expected

    def test_uncertain_rows_are_excluded_never_guessed(self) -> None:
        outcomes = {r.decide_binary(p / 100, 0.8, 0.2) for p in range(21, 80)}
        assert outcomes == {"excluded"}


class TestValidateThresholds:
    @pytest.mark.parametrize(
        ("pos", "neg", "field"),
        [
            (None, 0.2, "threshold_positive"),
            (0.5, None, "threshold_negative"),
            (1.2, 0.2, "threshold_positive"),
            (0.5, -0.1, "threshold_negative"),
            (0.2, 0.5, "threshold_negative"),
            (0.5, 0.5, "threshold_negative"),
            (float("nan"), 0.2, "threshold_positive"),
        ],
    )
    def test_refused(self, pos: float | None, neg: float | None, field: str) -> None:
        with pytest.raises(r.ThresholdsInvalid) as exc:
            r.validate_thresholds(pos, neg)
        assert exc.value.field == field

    def test_valid(self) -> None:
        r.validate_thresholds(0.5, 0.2)
        r.validate_thresholds(1.0, 0.0)


class TestTopLabel:
    LABELS = ["a", "b", "c"]

    def test_at_above_below_the_minimum(self) -> None:
        assert r.decide_top_label({"a": 0.6, "b": 0.3, "c": 0.1}, 0.6, self.LABELS) == "a"
        assert r.decide_top_label({"a": 0.7, "b": 0.2, "c": 0.1}, 0.6, self.LABELS) == "a"
        below = math.nextafter(0.6, 0.0)
        assert r.decide_top_label({"a": below, "b": 0.3, "c": 0.1}, 0.6, self.LABELS) == "excluded"

    def test_ties_go_to_the_first_label_in_order(self) -> None:
        dist = {"c": 0.4, "b": 0.4, "a": 0.2}
        assert r.decide_top_label(dist, 0.1, self.LABELS) == "b"
        assert r.decide_top_label(dist, 0.1, ["c", "b", "a"]) == "c"


def _wilson_by_quadratic(kept: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    """Independent oracle: the interval's ends are the roots of (p̂ - p)^2 = z^2 p (1 - p) / n."""
    ph = kept / n
    a = 1 + z * z / n
    b = -(2 * ph + z * z / n)
    c = ph * ph
    disc = math.sqrt(b * b - 4 * a * c)
    return (-b - disc) / (2 * a), (-b + disc) / (2 * a)


@pytest.mark.parametrize(("kept", "n"), [(188, 400), (1, 20), (399, 400), (25, 100)])
def test_wilson_interval_known_values(kept: int, n: int) -> None:
    share, lo, hi = r.wilson_interval(kept, n)
    want_lo, want_hi = _wilson_by_quadratic(kept, n)
    assert share == kept / n
    assert abs(lo - want_lo) < 1e-9 and abs(hi - want_hi) < 1e-9
    if (kept, n) == (188, 400):
        assert round(lo, 4) == 0.4216 and round(hi, 4) == 0.519


def test_wilson_edges() -> None:
    assert r.wilson_interval(0, 10)[1] == 0.0
    with pytest.raises(ValueError):
        r.wilson_interval(1, 0)


class TestApprovalNeeded:
    def test_5000_starts_5001_gated(self) -> None:
        kw = {"origin": "agent", "window_rows": 0, "threshold": 5000}
        assert r.approval_needed(rows_to_score=5000, **kw) is False  # type: ignore[arg-type]
        assert r.approval_needed(rows_to_score=5001, **kw) is True  # type: ignore[arg-type]

    def test_window_sums_runs_on_one_version(self) -> None:
        assert r.approval_needed(
            origin="agent", rows_to_score=3000, window_rows=3000, threshold=5000
        )
        assert not r.approval_needed(
            origin="agent", rows_to_score=3000, window_rows=0, threshold=5000
        )

    def test_operator_never_gated(self) -> None:
        assert not r.approval_needed(
            origin="operator", rows_to_score=50_000, window_rows=50_000, threshold=5000
        )


class TestIdentity:
    BASE = {
        "protocol": "openai_scoring",
        "model_id": "JEV-9B-decision",
        "model_revision": "b63f",
        "template_ref": "jev/noul-bare-v1@1",
        "question": "Is this funny?",
    }

    def test_each_part_changes_the_hash(self) -> None:
        base = r.identity_hash(r.labeler_identity(**self.BASE))  # type: ignore[arg-type]
        for key, value in [
            ("protocol", "tei_classification"),
            ("model_id", "other"),
            ("model_revision", "c0ffee"),
            ("template_ref", "jev/noul-bare-v1@2"),
            ("question", "Is this sad?"),
        ]:
            changed = r.identity_hash(r.labeler_identity(**{**self.BASE, key: value}))  # type: ignore[arg-type]
            assert changed != base, key

    def test_missing_revision_reads_not_reported(self) -> None:
        identity = r.labeler_identity(**{**self.BASE, "model_revision": None})  # type: ignore[arg-type]
        assert identity["model_revision"] == "not reported"

    def test_fingerprint_ignores_thresholds_and_tracks_settings(self) -> None:
        identity = r.labeler_identity(**self.BASE)  # type: ignore[arg-type]
        a = r.fingerprint(identity, {}, "n/a", "single")
        assert a == r.fingerprint(identity, {}, "n/a", "single")
        assert a != r.fingerprint(identity, {"seed": 1}, "n/a", "single")
        assert a != r.fingerprint(identity, {}, "json_schema", "single")
        assert a != r.fingerprint(identity, {}, "n/a", "packed")

    def test_reuse_needs_equal_reported_revisions(self) -> None:
        assert r.reuse_allowed("abc", "abc")
        assert not r.reuse_allowed("abc", "abd")
        assert not r.reuse_allowed(None, None)
        assert not r.reuse_allowed(None, "abc")


class TestJudgeRules:
    def test_swap_and_agree(self) -> None:
        assert r.swap_and_agree("A", "A") == "A"
        assert r.swap_and_agree("A", "B") == "position_inconsistent"
        assert r.swap_and_agree(None, "A") == "parse_failure"
        assert r.swap_and_agree("A", None) == "parse_failure"

    def test_parse_failure_boundary(self) -> None:
        assert not r.parse_failure_exceeded(10, 200, 0.05, 200)  # exactly 5%: not above
        assert r.parse_failure_exceeded(11, 200, 0.05, 200)
        assert not r.parse_failure_exceeded(50, 199, 0.05, 200)  # too few judged rows yet

    def test_aggregate(self) -> None:
        assert r.aggregate_verdict(["yes", "yes", "yes"]) == ("yes", None)
        assert r.aggregate_verdict(["yes", "yes", "no"]) == ("yes", "disagreement")
        assert r.aggregate_verdict(["yes", "no"]) == ("excluded", "disagreement")
        assert r.aggregate_verdict(["yes", None]) == ("yes", "missing_verdict")
        assert r.aggregate_verdict([None, None]) == ("excluded", "no_verdicts")
