"""Checks C-1..C-7, notes and push_allowed (FR-008.11–008.14, 008.61, 008.62; FTASKS 6.1–6.4).

Table-driven: every check × outcome × visibility. ``not_checked`` (an owner not built) is AMBER.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from src.services.publishing.checks import (
    CheckInputs,
    CheckOutcome,
    LabelerCheck,
    ModelTerms,
    Outcome,
    SourceLicence,
    evaluate_checks,
    push_allowed,
)
from src.services.publishing.feature_seams import (
    CHECKED,
    NOT_CHECKED,
    AuditFinding,
    CalibrationFinding,
    LabelerFinding,
    LeakageFinding,
    WarningFinding,
)
from src.services.publishing.licence_table import LicenceClass

PERMITS = LicenceClass.PERMITS


def _labeler(**kw: Any) -> LabelerCheck:
    base = {
        "fingerprint": "f" * 64,
        "name": "autotrust/JEV-9B",
        "pinned": True,
        "revision_reported": True,
        "calibration": CalibrationFinding(
            CHECKED,
            recorded=True,
            record_id="cal_1",
            verdict="passes",
            auroc={"value": 0.9, "ci_low": 0.88, "ci_high": 0.92, "n": 2000},
        ),
    }
    base.update(kw)
    return LabelerCheck(**base)  # type: ignore[arg-type]


def green(**overrides: Any) -> CheckInputs:
    inp = CheckInputs(
        visibility="public",
        sources=[SourceLicence("s1", "org/colbert", "cc-by-2.0", PERMITS, PERMITS)],
        token_scope="write",
        held_out_splits=["test"],
        leakage=LeakageFinding(CHECKED, 0),
        models=LabelerFinding(CHECKED, [], ["autotrust/JEV-9B"]),
        model_terms=[ModelTerms("autotrust/JEV-9B", "permits")],
        audit=AuditFinding(CHECKED, "complete"),
        labelers=[_labeler()],
        labeler_status=CHECKED,
        warnings=WarningFinding(CHECKED, [], []),
    )
    return replace(inp, **overrides)


def by_check(outcomes: list[CheckOutcome]) -> dict[str, Outcome]:
    return {o.check: o.outcome for o in outcomes}


def test_all_green_is_all_green_and_allowed_publicly() -> None:
    outcomes = evaluate_checks(green())
    assert set(by_check(outcomes).values()) == {Outcome.GREEN}
    assert [o.check for o in outcomes] == ["C-1", "C-2", "C-3", "C-4", "C-5", "C-6", "C-7"]
    assert push_allowed(outcomes, "public") == (True, [])


CASES: list[tuple[str, dict[str, Any], str, Outcome]] = [
    (
        "c1 private-only source",
        {"sources": [SourceLicence("s", "r", "unknown", LicenceClass.PRIVATE_ONLY, None)]},
        "C-1",
        Outcome.AMBER,
    ),
    (
        "c1 forbids source",
        {"sources": [SourceLicence("s", "r", "x", LicenceClass.FORBIDS, None)]},
        "C-1",
        Outcome.AMBER,
    ),
    ("c2 missing", {"token_scope": "missing"}, "C-2", Outcome.REFUSED),
    ("c2 read", {"token_scope": "read"}, "C-2", Outcome.REFUSED),
    ("c2 invalid", {"token_scope": "invalid"}, "C-2", Outcome.REFUSED),
    ("c2 cannot verify", {"token_scope": "cannot_verify"}, "C-2", Outcome.REFUSED),
    ("c2 namespace", {"token_scope": "namespace_denied"}, "C-2", Outcome.REFUSED),
    ("c3 crossing", {"leakage": LeakageFinding(CHECKED, 4)}, "C-3", Outcome.AMBER),
    ("c3 not checked", {"leakage": LeakageFinding(NOT_CHECKED, None)}, "C-3", Outcome.AMBER),
    (
        "c3 no held-out split",
        {"held_out_splits": [], "leakage": LeakageFinding(NOT_CHECKED, None)},
        "C-3",
        Outcome.GREEN,
    ),
    ("c4 no note", {"model_terms": [ModelTerms("autotrust/JEV-9B", None)]}, "C-4", Outcome.AMBER),
    (
        "c4 forbids",
        {"model_terms": [ModelTerms("autotrust/JEV-9B", "forbids")]},
        "C-4",
        Outcome.AMBER,
    ),
    ("c4 models unknown", {"models": LabelerFinding(NOT_CHECKED)}, "C-4", Outcome.AMBER),
    ("c4 no models", {"models": LabelerFinding(CHECKED, [], [])}, "C-4", Outcome.GREEN),
    (
        # D7: rows marked generated with no generation run anywhere in the lineage
        "c4 generated rows, no generator",
        {"models": LabelerFinding(CHECKED, [], []), "generated_rows": 3},
        "C-4",
        Outcome.AMBER,
    ),
    ("c5 not complete", {"audit": AuditFinding(CHECKED, "drawn")}, "C-5", Outcome.AMBER),
    ("c5 not checked", {"audit": AuditFinding(NOT_CHECKED, None)}, "C-5", Outcome.AMBER),
    (
        "c6 none recorded",
        {"labelers": [_labeler(calibration=CalibrationFinding(CHECKED, recorded=False))]},
        "C-6",
        Outcome.AMBER,
    ),
    (
        "c6 006 absent",
        {"labelers": [_labeler(calibration=CalibrationFinding(NOT_CHECKED, recorded=False))]},
        "C-6",
        Outcome.AMBER,
    ),
    (
        "c6 invalid",
        {
            "labelers": [
                _labeler(calibration=CalibrationFinding(CHECKED, recorded=True, verdict="invalid"))
            ]
        },
        "C-6",
        Outcome.AMBER,
    ),
    (
        "c6 insufficient is invalid",
        {
            "labelers": [
                _labeler(
                    calibration=CalibrationFinding(CHECKED, recorded=True, verdict="insufficient")
                )
            ]
        },
        "C-6",
        Outcome.AMBER,
    ),
    ("c6 labelers unknown", {"labeler_status": NOT_CHECKED, "labelers": []}, "C-6", Outcome.AMBER),
    ("c6 no labelers", {"labelers": []}, "C-6", Outcome.GREEN),
    (
        "c7 warning",
        {"warnings": WarningFinding(CHECKED, [{"column": "source_label"}], [])},
        "C-7",
        Outcome.AMBER,
    ),
    (
        "c7 invalid column",
        {"warnings": WarningFinding(CHECKED, [], ["length"])},
        "C-7",
        Outcome.AMBER,
    ),
    ("c7 not checked", {"warnings": WarningFinding(NOT_CHECKED)}, "C-7", Outcome.AMBER),
]


@pytest.mark.parametrize(("name", "change", "check", "expected"), CASES, ids=[c[0] for c in CASES])
@pytest.mark.parametrize("visibility", ["private", "public"])
def test_each_check_outcome_and_what_it_allows(
    name: str, change: dict[str, Any], check: str, expected: Outcome, visibility: str
) -> None:
    outcomes = evaluate_checks(green(visibility=visibility, **change))
    assert by_check(outcomes)[check] is expected, name
    others = {c: o for c, o in by_check(outcomes).items() if c != check and c.startswith("C-")}
    assert set(others.values()) == {Outcome.GREEN}, others
    allowed, blocking = push_allowed(outcomes, visibility)
    if expected is Outcome.REFUSED:
        assert not allowed and [b.check for b in blocking] == [check]
    elif expected is Outcome.AMBER:
        assert allowed is (visibility == "private"), "amber refuses public only"
    else:
        assert allowed


def test_every_not_checked_outcome_says_so_and_names_its_owner() -> None:
    outcomes = evaluate_checks(
        green(
            leakage=LeakageFinding(NOT_CHECKED, None),
            audit=AuditFinding(NOT_CHECKED, None),
            warnings=WarningFinding(NOT_CHECKED),
        )
    )
    amber = [o for o in outcomes if o.outcome is Outcome.AMBER]
    assert {o.check for o in amber} == {"C-3", "C-5", "C-7"}
    assert {o.evidence["owner"] for o in amber} == {"004", "006"}
    assert all(o.evidence["not_checked"] for o in amber)


def test_a_failing_verdict_is_a_note_never_a_refusal() -> None:
    failing = _labeler(
        calibration=CalibrationFinding(
            CHECKED,
            recorded=True,
            record_id="cal_2",
            verdict="fails",
            auroc={"value": 0.61, "ci_low": 0.58, "ci_high": 0.64, "n": 2000},
        )
    )
    outcomes = evaluate_checks(green(labelers=[failing]))
    assert by_check(outcomes)["C-6"] is Outcome.GREEN
    assert by_check(outcomes)["N-failing_verdict"] is Outcome.NOTE
    assert "0.610" in next(o.reason for o in outcomes if o.check == "N-failing_verdict")
    assert push_allowed(outcomes, "public")[0]


def test_unpinned_and_unreported_revision_are_notes() -> None:
    outcomes = evaluate_checks(green(labelers=[_labeler(pinned=False, revision_reported=False)]))
    assert by_check(outcomes)["N-unpinned_labeler"] is Outcome.NOTE
    assert by_check(outcomes)["N-revision_not_reported"] is Outcome.NOTE
    assert push_allowed(outcomes, "public")[0]


def test_a_changed_licence_since_build_is_a_note_with_both_classes() -> None:
    changed = SourceLicence("s1", "org/x", "mit", PERMITS, LicenceClass.PRIVATE_ONLY)
    outcomes = evaluate_checks(green(sources=[changed]))
    note = next(o for o in outcomes if o.check == "N-licence_changed_since_build")
    assert note.evidence == {
        "source_id": "s1",
        "built": "private_only",
        "current": "permits_redistribution",
    }
    back = SourceLicence("s1", "org/x", "mit", LicenceClass.PRIVATE_ONLY, PERMITS)
    outcomes = evaluate_checks(green(sources=[back]))
    assert by_check(outcomes)["C-1"] is Outcome.AMBER
    assert "N-licence_changed_since_build" in by_check(outcomes)


def test_refused_refuses_private_and_amber_refuses_only_public() -> None:
    refused = CheckOutcome("C-2", Outcome.REFUSED, "r", "n")
    amber = CheckOutcome("C-7", Outcome.AMBER, "a", "n")
    note = CheckOutcome("N-x", Outcome.NOTE, "n", "n")
    assert push_allowed([refused], "private") == (False, [refused])
    assert push_allowed([amber], "private") == (True, [])
    assert push_allowed([amber], "public") == (False, [amber])
    assert push_allowed([note], "public") == (True, [])


def test_outcomes_round_trip_through_their_snapshot() -> None:
    for o in evaluate_checks(green(token_scope="read")):
        assert CheckOutcome.from_dict(o.as_dict()) == o


def test_c4_says_which_rows_were_generated_and_by_whom() -> None:
    """D7: C-4 names the generated row count, the generator and the labeler, in its reason and
    evidence, whatever its outcome."""
    from src.services.publishing.checks import c4_model_terms

    inp = green(
        models=LabelerFinding(
            CHECKED,
            [{"model_id": "judge-9b", "role": "judge", "run_ids": ["lr_1"]}],
            ["gen-7b", "judge-9b"],
        ),
        model_terms=[ModelTerms("gen-7b", "permits"), ModelTerms("judge-9b", None)],
        generated_rows=12,
        generators=[{"model_id": "gen-7b", "generation_run_id": "gr_1"}],
    )
    out = c4_model_terms(inp)
    assert out.outcome is Outcome.AMBER and out.evidence["missing"] == ["judge-9b"]
    assert "12 generated row(s) from gen-7b; labels from judge judge-9b" in out.reason
    assert out.evidence["generated_rows"] == 12
    assert out.evidence["generation_runs"] == ["gr_1"]
    assert out.evidence["labelers"] == [
        {"model_id": "judge-9b", "role": "judge", "run_ids": ["lr_1"]}
    ]
    none = c4_model_terms(green(models=LabelerFinding(CHECKED, [], []), generated_rows=12))
    assert none.outcome is Outcome.AMBER
    assert none.reason.startswith("12 row(s) are marked generated, but no generation run is bound")
