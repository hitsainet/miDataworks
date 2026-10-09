"""009's pure decision rules (FTASKS 3.1 - 3.5, 3.8, 3.9).

Fixtures DIFFER in the dimension each test checks (FTDD 009 section 10): length fixtures whose
roles share one length are forbidden, mapping fixtures carry values the mapping does not name.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from src.services.detector_sets import checks, label_rules, length, plan, separation, verdicts
from src.services.detector_sets.checks import (
    CheckInputs,
    LabelerStatus,
    LeakageFacts,
    RoleFacts,
    WarningFacts,
)
from src.services.detector_sets.role_mapping import MISTUDIO_ROLE

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "detector_sets"


# --- 3.1 role mapping pinned against miStudio's literals --------------------------------------


def test_role_mapping_uses_only_mistudio_literals() -> None:
    literals = json.loads((FIXTURES / "mistudio_literals.json").read_text())
    roles = set(literals["DatasetRole"])
    distributions = set(literals["Distribution"])
    for mine, (theirs, distribution) in MISTUDIO_ROLE.items():
        assert theirs in roles, mine
        assert distribution is None or distribution in distributions, mine
        # miStudio refuses a distribution on any role but eval (validator lines 132-140).
        assert (distribution is not None) == (theirs == "eval"), mine
    assert MISTUDIO_ROLE["ood_eval"] == ("eval", "out_of_distribution")
    assert MISTUDIO_ROLE["id_test"] == ("eval", "in_distribution")
    assert set(MISTUDIO_ROLE) == {"train", "id_test", "ood_eval", "calibration_negatives"}


# --- 3.2 label rules -------------------------------------------------------------------------


def codes(problems: list[label_rules.MappingProblem]) -> list[str]:
    return [p.code for p in problems]


def test_an_unmapped_value_present_in_the_split_is_named() -> None:
    out = label_rules.check_mapping(
        "train",
        {"humorous": 10, "not_humorous": 12, "unsure": 3},
        {"humorous": "positive", "not_humorous": "negative"},
    )
    assert codes(out) == ["unmapped_value"]
    assert out[0].value == "unsure"


def test_no_positive_and_no_negative_are_each_refused() -> None:
    assert codes(
        label_rules.check_mapping("id_test", {"a": 5, "b": 5}, {"a": "negative", "b": "excluded"})
    ) == ["no_positive"]
    assert codes(
        label_rules.check_mapping("ood_eval", {"a": 5, "b": 5}, {"a": "positive", "b": "excluded"})
    ) == ["no_negative"]


def test_a_positive_mapped_value_absent_from_the_split_does_not_count() -> None:
    out = label_rules.check_mapping("train", {"b": 5}, {"a": "positive", "b": "negative"})
    assert codes(out) == ["no_positive"]


def test_positive_in_calibration_is_refused_even_when_absent_from_the_split() -> None:
    out = label_rules.check_mapping(
        "calibration_negatives", {"x": 9}, {"x": "negative", "y": "positive"}
    )
    assert codes(out) == ["positive_in_calibration"]
    # calibration negatives need no positive
    assert label_rules.check_mapping("calibration_negatives", {"x": 9}, {"x": "negative"}) == []


def test_unknown_target_is_refused() -> None:
    out = label_rules.check_mapping("train", {"a": 1, "b": 1}, {"a": "positive", "b": "maybe"})
    assert "unknown_target" in codes(out)


def test_numeric_and_boolean_labels_compare_as_strings() -> None:
    assert (
        label_rules.check_mapping("train", {1: 4, 0: 6}, {"1": "positive", "0": "negative"}) == []
    )
    assert (
        label_rules.check_mapping(
            "train", {True: 4, False: 6}, {"true": "positive", "false": "negative"}
        )
        == []
    )
    assert label_rules.expected_counts(
        {1: 4, 0: 6, 2: 1}, {"1": "positive", "0": "negative", "2": "excluded"}
    ) == {
        "positive": 4,
        "negative": 6,
        "excluded": 1,
    }


# --- 3.3 length profile and overlap -----------------------------------------------------------


def test_profile_reports_quantiles_n_and_a_histogram() -> None:
    p = length.profile(list(range(1, 101)))
    assert p.n == 100
    assert p.quantiles["p50"] == pytest.approx(50.5)
    assert p.quantiles["p05"] == pytest.approx(5.95)
    assert sum(p.histogram["counts"]) == 100
    assert len(p.permille) == 1001
    assert length.profile([]).n == 0


def test_overlap_of_different_lengths_is_low_and_symmetric_in_its_minimum() -> None:
    rng = np.random.default_rng(1)
    chat = rng.normal(1500, 400, 2000).clip(50)
    headlines = rng.normal(66, 15, 2000).clip(10)
    ab = length.overlap(chat, headlines)
    ba = length.overlap(headlines, chat)
    assert ab.figure == ba.figure  # min of the two shares does not depend on the order
    assert ab.cal_in_ref == ba.ref_in_cal and ab.ref_in_cal == ba.cal_in_ref
    assert ab.figure < 0.05


def test_identical_distributions_overlap_at_the_band_ceiling_and_disjoint_at_zero() -> None:
    values = np.arange(1, 1001, dtype=float)
    same = length.overlap(values, values)
    # 90% of each array lies inside its own 5th-95th band, so identical gives ~0.9, the ceiling.
    assert same.figure == pytest.approx(0.9, abs=0.002)
    assert length.overlap(np.arange(1, 100.0), np.arange(1000, 1100.0)).figure == 0.0


def test_overlap_on_permille_points_matches_the_full_arrays() -> None:
    rng = np.random.default_rng(7)
    a = rng.lognormal(4, 0.6, 30_000)
    b = rng.lognormal(4.3, 0.5, 20_000)
    full = length.overlap(a, b)
    sampled = length.overlap(length.profile(a).permille, length.profile(b).permille)
    assert abs(full.figure - sampled.figure) <= 0.002


def test_finest_fpr_is_one_over_n() -> None:
    assert length.finest_fpr(2000) == 1 / 2000
    assert length.finest_fpr(0) is None


# --- 3.4 checks ------------------------------------------------------------------------------


def role(role_id: str, kind: str, *, ok: bool = True, problems: tuple = ()) -> RoleFacts:
    return RoleFacts(role_id, kind, role_id, ok, "completed" if ok else "building", problems, {})


FULL_ROLES = (
    role("r-train", "train"),
    role("r-test", "id_test"),
    role("r-ood", "ood_eval"),
    role("r-cal", "calibration_negatives"),
)


def inputs(**over: object) -> CheckInputs:
    base: dict[str, object] = {
        "roles": FULL_ROLES,
        "warnings": WarningFacts(),
        "leakage": LeakageFacts({}),
        "overlap": length.OverlapResult(0.8, 0.8, 0.85, (40.0, 110.0), (35.0, 100.0)),
        "overlap_min": 0.5,
        "synthetic_rows": {},
        "labelers": (),
        "no_record_needed": ("r-ood",),
        "negatives_basis": {
            "kind": "labeler_filtered",
            "labeler_identity_hash": "a" * 64,
            "rule": "p<=0.2",
        },
    }
    base.update(over)
    return CheckInputs(**base)  # type: ignore[arg-type]


def outcome(code: str, **over: object) -> str:
    return next(o for o in checks.evaluate(inputs(**over)) if o.code == code).outcome


def test_a_clean_set_is_all_green_and_allowed() -> None:
    out = checks.evaluate(inputs())
    assert [o.code for o in out] == [f"D-{i}" for i in range(1, 9)]
    assert {o.outcome for o in out} == {"green"}
    assert checks.send_allowed(out)


def test_d1_refuses_a_missing_role_a_second_train_no_ood_and_an_incomplete_version() -> None:
    assert outcome("D-1", roles=FULL_ROLES[1:]) == "refused"
    assert outcome("D-1", roles=(*FULL_ROLES, role("r-train2", "train"))) == "refused"
    assert outcome("D-1", roles=tuple(r for r in FULL_ROLES if r.role != "ood_eval")) == "refused"
    assert outcome("D-1", roles=(role("r-train", "train", ok=False), *FULL_ROLES[1:])) == "refused"
    assert outcome("D-1", monitored_ref_named=False) == "refused"
    assert outcome("D-1", roles=(*FULL_ROLES, role("r-ood2", "ood_eval"))) == "green"


def test_d2_refuses_any_mapping_problem() -> None:
    bad = label_rules.MappingProblem("unmapped_value", "x", "no mapping")
    roles = (role("r-train", "train", problems=(bad,)), *FULL_ROLES[1:])
    assert outcome("D-2", roles=roles) == "refused"


def test_d3_refuses_a_warning_and_an_invalid_audit() -> None:
    assert (
        outcome("D-3", warnings=WarningFacts([{"column": "source", "figure": 0.91}])) == "refused"
    )
    assert outcome("D-3", warnings=WarningFacts((), ["(audit insufficient_rows)"])) == "refused"


def test_d4_refuses_any_crossing_pair() -> None:
    assert outcome("D-4", leakage=LeakageFacts({"id_test|train": 2})) == "refused"
    assert outcome("D-4", leakage=LeakageFacts({"id_test|train": 0})) == "green"


def test_d5_is_a_note_below_tolerance_and_never_refuses() -> None:
    low = length.OverlapResult(0.02, 0.02, 0.4, (40.0, 110.0), (400.0, 3000.0))
    assert outcome("D-5", overlap=low) == "note"
    assert outcome("D-5", overlap=low, overlap_min=None) == "note"
    assert outcome("D-5", overlap=None) == "note"
    assert checks.send_allowed(checks.evaluate(inputs(overlap=low)))


def test_d6_and_d8_are_notes() -> None:
    assert outcome("D-6", synthetic_rows={"r-ood": 12}) == "note"
    assert outcome("D-8", negatives_basis={"kind": "assumed_negative"}) == "note"
    assert checks.send_allowed(
        checks.evaluate(
            inputs(synthetic_rows={"r-ood": 12}, negatives_basis={"kind": "assumed_negative"})
        )
    )


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [
        ("none", "refused"),
        ("invalid", "refused"),
        ("insufficient", "refused"),
        ("fails", "note"),
        ("passes", "green"),
    ],
)
def test_d7_outcome_per_verdict(verdict: str, expected: str) -> None:
    lab = LabelerStatus(
        "f" * 64,
        "jev-9b",
        ("r-train",),
        verdict,
        None,
        {"value": 0.7, "ci_low": 0.68, "ci_high": 0.72, "n": 900},
    )
    assert outcome("D-7", labelers=(lab,)) == expected


# --- 3.5 plan, registration bodies, approval digest ------------------------------------------


def body(role_kind: str, **over: object) -> dict[str, object]:
    args: dict[str, object] = {
        "name": "humor train",
        "dataset_id": "d1",
        "config": None,
        "split": "train",
        "input_column": "text",
        "label_column": "label",
        "label_mapping": {"humorous": "positive", "not_humorous": "negative"},
        "role": role_kind,
        "pair_column": None,
    }
    args.update(over)
    return plan.registration_body(**args)  # type: ignore[arg-type]


def test_registration_body_keys_are_exactly_the_pinned_set() -> None:
    base = set(plan.REGISTRATION_KEYS)
    assert set(body("train")) == base
    assert set(body("calibration_negatives")) == base
    assert set(body("id_test")) == base | {"distribution"}
    assert body("ood_eval")["distribution"] == "out_of_distribution"
    assert set(body("ood_eval", pair_column="pair_id")) == base | {"distribution", "pair_column"}
    assert "keyword_filter" not in body("ood_eval", pair_column="pair_id")


def test_the_manifest_is_sent_only_when_miStudio_serves_it() -> None:
    manifest = {"schema": "midataworks.dataset-version/v1"}
    assert "dataset_version_manifest" not in body("train", manifest=manifest, manifest_served=False)
    assert (
        body("train", manifest=manifest, manifest_served=True)["dataset_version_manifest"]
        == manifest
    )


def test_a_body_with_an_unpinned_key_is_refused() -> None:
    with pytest.raises(plan.RegistrationBodyError):
        plan.check_body_keys({"name": "x", "keyword_filter": {"any": ["joke"]}})


def _role_input(rid: str, kind: str, version: str, split: str) -> plan.RoleInput:
    return plan.RoleInput(
        rid,
        kind,
        version,
        split,
        "text",
        "label",
        {"a": "positive", "b": "negative"},
        None,
        f"set {rid}",
        {"positive": 1, "negative": 1},
    )


def _plan(roles: list[plan.RoleInput], units: list[plan.PublishUnit]) -> dict[str, object]:
    return plan.build_plan(
        roles, units, mistudio_base_url="http://m", manifest_served=False
    ).as_dict()


def test_reordering_inputs_gives_the_same_digest_and_any_change_a_new_one() -> None:
    roles = [
        _role_input("r1", "train", "v1", "train"),
        _role_input("r2", "id_test", "v1", "test"),
        _role_input("r3", "ood_eval", "v2", "test"),
    ]
    units = [
        plan.PublishUnit("v1", "ns/a-v1", "label", "private", "b1", "", "d" * 64),
        plan.PublishUnit("v2", "ns/b-v1", "label", "private", "b2", "", "e" * 64),
    ]
    one = plan.approval_digest(_plan(roles, units), set_id="dts_1", snapshot_sha256="0" * 64)
    two = plan.approval_digest(
        _plan(roles[::-1], units[::-1]), set_id="dts_1", snapshot_sha256="0" * 64
    )
    assert one == two
    changed_units = [
        units[0],
        plan.PublishUnit("v2", "ns/b-v1", "label", "public", "b2", "", "f" * 64),
    ]
    assert (
        plan.approval_digest(_plan(roles, changed_units), set_id="dts_1", snapshot_sha256="0" * 64)
        != one
    )
    changed_roles = [
        *roles[:2],
        plan.RoleInput(
            "r3",
            "ood_eval",
            "v2",
            "test",
            "text",
            "label",
            {"a": "negative", "b": "positive"},
            None,
            "set r3",
            {},
        ),
    ]
    assert (
        plan.approval_digest(_plan(changed_roles, units), set_id="dts_1", snapshot_sha256="0" * 64)
        != one
    )
    assert (
        plan.approval_digest(_plan(roles, units), set_id="dts_2", snapshot_sha256="0" * 64) != one
    )


def test_roles_sharing_a_repository_and_split_share_one_download() -> None:
    roles = [
        _role_input("r1", "train", "v1", "train"),
        _role_input("r2", "id_test", "v1", "test"),
        _role_input("r4", "calibration_negatives", "v1", "test"),
    ]
    units = [plan.PublishUnit("v1", "ns/a-v1", "label", "private", "b1", "", "d" * 64)]
    downloads = _plan(roles, units)["download_units"]
    assert [(d["split"], d["role_ids"]) for d in downloads] == [("test", ["r2", "r4"]), ("train", ["r1"])]  # type: ignore[index]


# --- 3.8 separation --------------------------------------------------------------------------


def test_separation_rules() -> None:
    a = separation.ProbeTrace("pm_a", "m1", 12, {"k1", "k2"})
    b = separation.ProbeTrace("pm_b", "m1", 16, {"k3"})
    assert separation.separate(a, b).verdict == "separate"
    assert (
        separation.separate(a, separation.ProbeTrace("pm_c", "m1", 16, {"k2"})).verdict
        == "not_separate"
    )
    assert (
        separation.separate(
            a, separation.ProbeTrace("pm_d", "m1", 16, None, "no registration")
        ).verdict
        == "unprovable"
    )
    same_layer = separation.separate(a, separation.ProbeTrace("pm_e", "m1", 12, {"k9"}))
    assert same_layer.verdict == "separate" and same_layer.warnings
    assert separation.separate(a, a).verdict == "not_separate"


# --- 3.9 verdict mapping ---------------------------------------------------------------------


def test_verdict_mapping_and_the_boundary() -> None:
    assert verdicts.map_verdict({"verdict": True, "provisional": False}).outcome == "positive"
    assert verdicts.map_verdict({"verdict": False, "provisional": False}).outcome == "negative"
    skipped = verdicts.map_verdict(
        {"verdict": None, "provisional": False, "not_scored_reason": "no_scored_tokens"}
    )
    assert skipped.outcome == "skipped" and skipped.reason == "no_scored_tokens"
    prov = verdicts.map_verdict({"verdict": True, "provisional": True})
    assert prov.outcome == "excluded" and prov.provisional
    # P-03: a score exactly equal to the threshold comes back verdict true and maps positive.
    assert verdicts.fires(27.613433837890625, 27.613433837890625)
    on_bar = {
        "score": 27.613433837890625,
        "threshold": 27.613433837890625,
        "verdict": True,
        "provisional": False,
    }
    assert verdicts.map_verdict(on_bar).outcome == "positive"
    assert not verdicts.fires(27.6, 27.613433837890625)


def test_overlap_is_the_smaller_share_when_the_shares_differ() -> None:
    """Control C06 survived: every earlier fixture had two near-equal shares."""
    narrow = np.linspace(60, 70, 500)  # calibration lengths all inside the reference's band
    wide = np.linspace(20, 200, 500)  # reference lengths mostly outside the calibration's band
    result = length.overlap(narrow, wide)
    assert result.cal_in_ref == 1.0
    assert result.ref_in_cal < 0.1
    assert result.figure == result.ref_in_cal
