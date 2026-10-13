"""Every branch of the gate-2 verdict (006 FTASKS 5.1 – 5.3; FTDD 006 section 10.2).

Fixtures are chosen so that the mutations in FTDD 006 section 10.3 change the answer: values sit
EXACTLY on each bar (X-03 ``>=``), the point estimate and the lower bound fall on opposite sides of
C3, and the rater's point and lower bound fall on opposite sides of the comparison.
"""

from __future__ import annotations

from src.services.calibration.checks import CheckResult
from src.services.calibration.metrics import Interval
from src.services.calibration.verdict import Figures, decide


def iv(value: float, lo: float, hi: float) -> Interval:
    return Interval(value, lo, hi, 1000, 2000, 0)


def check(metric: str, result: str = "pass", check_id: str = "row_alignment") -> CheckResult:
    return CheckResult(check_id, 1, metric, result, {}, "rule", None if result != "fail" else "x")  # type: ignore[arg-type]


PASSING = [check("auroc"), check("ceiling", check_id="ceiling_circularity")]


def test_invalid_beats_a_passing_auroc() -> None:
    v = decide(Figures(iv(0.95, 0.94, 0.96)), [check("auroc", "fail")], None)
    assert v.verdict == "invalid" and v.rule is None
    assert v.numbers["failed_checks"] == ["row_alignment"]


def test_insufficient_without_auroc_even_though_both_classes_failed() -> None:
    v = decide(Figures(None), [check("auroc", "fail", "both_classes")], None)
    assert v.verdict == "insufficient"


def test_operator_target_met_exactly_on_the_bar() -> None:
    v = decide(Figures(iv(0.80, 0.75, 0.85)), PASSING, 0.75)
    assert (v.verdict, v.rule) == ("passes", "operator_target")
    assert v.numbers["compared"] == 0.75 and v.numbers["threshold"] == 0.75


def test_operator_target_missed_names_the_target_rule() -> None:
    v = decide(Figures(iv(0.90, 0.749, 0.95)), PASSING, 0.75)
    assert (v.verdict, v.rule) == ("fails", "operator_target")
    assert v.numbers["compared"] == 0.749 and v.numbers["threshold"] == 0.75


def test_an_operator_target_compares_the_lower_bound_not_the_point() -> None:
    v = decide(Figures(iv(0.80, 0.70, 0.90)), PASSING, 0.75)
    assert v.verdict == "fails"


def test_rater_rule_met_exactly_at_the_raters_lower_bound() -> None:
    figs = Figures(iv(0.60, 0.55, 0.65), iv(0.740, 0.731, 0.749), iv(0.731, 0.72, 0.74))
    v = decide(figs, PASSING, None)
    assert (v.verdict, v.rule) == ("passes", "held_out_rater")
    assert v.numbers["compared"] == 0.731 and v.numbers["threshold"] == 0.731


def test_rater_rule_compares_with_the_lower_bound_not_the_point() -> None:
    """Comparison 0.735 is below the rater's point 0.740 but above its lower bound 0.731: passes."""
    figs = Figures(iv(0.60, 0.55, 0.65), iv(0.740, 0.731, 0.749), iv(0.735, 0.72, 0.75))
    assert decide(figs, PASSING, None).verdict == "passes"


def test_rater_rule_point_above_but_comparison_below_lower_bound_fails() -> None:
    figs = Figures(iv(0.60, 0.55, 0.65), iv(0.740, 0.731, 0.749), iv(0.7309, 0.72, 0.74))
    v = decide(figs, PASSING, None)
    assert (v.verdict, v.rule) == ("fails", "held_out_rater")


def test_default_c3_exactly_at_point_seven_on_the_lower_bound() -> None:
    v = decide(Figures(iv(0.72, 0.70, 0.74)), PASSING, None)
    assert (v.verdict, v.rule) == ("passes", "default_c3")
    assert v.numbers["threshold"] == 0.70 and v.numbers["compared"] == 0.70


def test_c3_compares_the_lower_bound_not_the_point() -> None:
    v = decide(Figures(iv(0.72, 0.69, 0.74)), PASSING, None)
    assert (v.verdict, v.rule) == ("fails", "default_c3")


def test_c3_is_not_applied_when_a_valid_ceiling_exists() -> None:
    """AUROC lower bound 0.80 would pass C3, but a valid ceiling the labeler misses decides."""
    figs = Figures(iv(0.85, 0.80, 0.90), iv(0.95, 0.94, 0.96), iv(0.85, 0.80, 0.90))
    v = decide(figs, PASSING, None)
    assert (v.verdict, v.rule) == ("fails", "held_out_rater")


def test_c3_applies_when_the_ceiling_failed_its_check() -> None:
    figs = Figures(iv(0.85, 0.80, 0.90), iv(0.995, 0.99, 1.0), iv(0.85, 0.80, 0.90))
    checks = [check("auroc"), check("ceiling", "fail", "ceiling_circularity")]
    v = decide(figs, checks, None)
    assert (v.verdict, v.rule) == ("passes", "default_c3")
    assert v.numbers["ceiling_valid"] is False


def test_c3_is_not_applied_when_a_target_is_set() -> None:
    v = decide(Figures(iv(0.80, 0.72, 0.85)), PASSING, 0.75)
    assert (v.verdict, v.rule) == ("fails", "operator_target")


def test_numbers_carry_every_compared_value_and_threshold() -> None:
    figs = Figures(iv(0.753, 0.742, 0.765), iv(0.740, 0.731, 0.749), iv(0.755, 0.74, 0.77))
    v = decide(figs, PASSING, None)
    assert v.numbers["auroc"] == {"value": 0.753, "ci_low": 0.742, "ci_high": 0.765}
    assert v.numbers["ceiling"]["ci_low"] == 0.731
    assert v.numbers["comparison_auroc"]["value"] == 0.755
    assert v.numbers["target"] is None and v.numbers["c3_default_lower_bound"] == 0.70
    assert v.numbers["compared"] == 0.755 and v.numbers["threshold"] == 0.731
