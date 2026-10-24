"""Checks D-1 to D-9, D-11 and the one send gate (FR-009.14, FR-009.15; FTDD 009 section 5.3; P-01, P-02).

``evaluate(inputs) -> list[CheckOutcome]`` and ``send_allowed(outcomes) -> bool`` are the ONLY code
that decides whether a send may start. Both are pure: the service gathers the facts
(``set_service.gather_check_inputs``) and this module applies the rules.

Refusing: D-1, D-2, D-3 (any shortcut warning, or an invalid audit), D-4 (any pair crossing two
roles), D-7 (no record, ``invalid`` or ``insufficient``), D-11 (017 FR-017.8: a kept evaluation
role from another version shares a directing value or a group with the new training role). Notes:
D-5, D-6, D-8, D-7 ``fails`` and D-9 (015: wording alone; NEVER refuses, ENH-005 D7, D55). D-6 names
the disjointness proof when every synthetic evaluation row sits in a value-directed held-out split
with a passing proof (017 FR-017.4, citing the amended rule). D-10 arrives with 017 slice 7.

A fact that could not be gathered is never a pass: an audit 004 could not compute arrives as
``invalid`` and refuses (D-3).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from ..curation.held_out_proof import CITATION
from . import copy
from .label_rules import MappingProblem
from .length import OverlapResult

Outcome = Literal["green", "note", "refused"]


@dataclass(frozen=True)
class CheckOutcome:
    code: str
    title: str
    outcome: Outcome
    figure: str | None
    reason: str
    next_step: str | None
    source: str
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RoleFacts:
    role_id: str
    role: str
    name: str
    version_ok: bool
    version_state: str
    problems: tuple[MappingProblem, ...]
    counts: Mapping[str, int]


@dataclass(frozen=True)
class WarningFacts:
    warnings: Sequence[Mapping[str, Any]] = ()
    invalid: Sequence[str] = ()
    #: Columns 004 did not audit because they are the label's provenance: ``label_source``
    #: (declared by a role's ``label_source_columns``) or ``label_derived`` (a labeler wrote them).
    #: D-3 reports every one, so an exclusion is never silent.
    excluded: Sequence[Mapping[str, Any]] = ()


@dataclass(frozen=True)
class LeakageFacts:
    #: "<roleA>|<roleB>" -> crossing pair count, roles different.
    crossing: Mapping[str, int] = field(default_factory=dict)
    #: role name -> the evaluation role of THIS set its version was mined from (FR-009.59).
    mined: Mapping[str, str] = field(default_factory=dict)
    #: "<role>: <set> / <role>" for versions mined from another set's evaluation data.
    mined_elsewhere: Sequence[str] = ()


@dataclass(frozen=True)
class LabelerStatus:
    fingerprint: str
    name: str
    roles: tuple[str, ...]
    #: ``none`` (no record), or 006's verdict: passes / fails / invalid / insufficient.
    verdict: str
    record_id: str | None = None
    auroc: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class WordingComparison:
    """One role and one reference (an out-of-distribution role) scored at the same size (D55)."""

    reference: str
    n: int
    auroc: float
    reference_auroc: float


@dataclass(frozen=True)
class WordingFacts:
    """D-9 for one training or in-distribution test role (015 FR-015.15)."""

    role: str
    name: str
    n: int
    #: None when not computed (``reason``).
    auroc: float | None
    grouped: bool
    reason: str | None = None
    comparisons: tuple[WordingComparison, ...] = ()


#: D55: with a reference, warn above the reference's figure plus this margin at equal size...
WORDING_MARGIN = 0.02
#: ...and without one, warn at or above this figure.
WORDING_ABSOLUTE = 0.97


@dataclass(frozen=True)
class CrossVersionFacts:
    """D-11 for one kept role whose version is not the training role's (FR-017.8; D42, D45)."""

    role: str
    version_id: str
    version_label: str
    training_role: str
    training_version_id: str
    training_version_label: str
    columns: tuple[str, ...]
    #: ``(column, held-out value, training rows)`` found in the new training role.
    intersection: tuple[tuple[str, str, int], ...] = ()
    #: group column -> groups found in both roles.
    shared_groups: Mapping[str, int] = field(default_factory=dict)
    #: Directing or group columns a role's files lack (Review R1 F2): absent from the training
    #: rows, a value or a group reads as "not found" whether or not the same rows are there, so the
    #: proof cannot be made and the role is not disjoint by default.
    missing_columns: tuple[str, ...] = ()
    #: Rows of the kept role that are also training rows (row keys): calibration negatives since
    #: D42, every kept evaluation role since D45, value-directed or not.
    shared_rows: int = 0
    #: The kept role's kind (``id_test``, ``ood_eval``, ``calibration_negatives``).
    kind: str = "ood_eval"

    @property
    def disjoint(self) -> bool:
        return (
            not int(self.shared_rows)
            and not self.missing_columns
            and not self.intersection
            and not any(int(n) for n in self.shared_groups.values())
        )


@dataclass(frozen=True)
class CheckInputs:
    roles: Sequence[RoleFacts]
    warnings: WarningFacts
    leakage: LeakageFacts
    #: None when the overlap could not be computed (no monitored reference or no calibration role).
    overlap: OverlapResult | None
    overlap_min: float | None
    #: eval role name -> synthetic row count in its version (002 lineage, ``_dw_origin``).
    synthetic_rows: Mapping[str, int]
    labelers: Sequence[LabelerStatus]
    #: roles whose labels need no record (human- or source-labelled; FR-009.80).
    no_record_needed: Sequence[str]
    negatives_basis: Mapping[str, Any] | None
    #: The monitored text the probe will watch (FR-009.8); a set without one cannot be sent.
    monitored_ref_named: bool = True
    #: D-9 (015): the training and in-distribution test roles' wording figures.
    wording: Sequence[WordingFacts] = ()
    #: D-6 (017 FR-017.4): eval role name -> the passing proof of the value-directed held-out split
    #: it binds (only roles whose split has one).
    proven_splits: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    #: D-11 (017 FR-017.8): every kept evaluation role bound to another version than training's.
    cross_version: Sequence[CrossVersionFacts] = ()


def _d1(inputs: CheckInputs) -> CheckOutcome:
    by_role: dict[str, int] = {}
    for r in inputs.roles:
        by_role[r.role] = by_role.get(r.role, 0) + 1
    missing = [r for r in ("train", "id_test", "calibration_negatives") if by_role.get(r, 0) != 1]
    no_ood = by_role.get("ood_eval", 0) < 1
    bad_versions = [r.name for r in inputs.roles if not r.version_ok]
    no_monitored = not inputs.monitored_ref_named
    if missing or no_ood or bad_versions or no_monitored:
        return CheckOutcome(
            "D-1",
            "Roles complete",
            "refused",
            None,
            copy.roles_incomplete(missing, no_ood, bad_versions, no_monitored),
            "Bind exactly one training, in-distribution test and calibration negatives role and "
            "at least one out-of-distribution role, each to a completed version, and name the "
            "monitored text.",
            "R-03.49",
            {
                "missing": missing,
                "no_ood": no_ood,
                "versions_not_completed": bad_versions,
                "monitored_ref_missing": no_monitored,
            },
        )
    return CheckOutcome(
        "D-1",
        "Roles complete",
        "green",
        f"{len(inputs.roles)} roles",
        "Every role is bound to a completed version.",
        None,
        "R-03.49",
    )


def _d2(inputs: CheckInputs) -> CheckOutcome:
    problems = [
        {"role": r.name, "code": p.code, "value": p.value, "message": p.message}
        for r in inputs.roles
        for p in r.problems
    ]
    if problems:
        first = problems[0]
        return CheckOutcome(
            "D-2",
            "Label mappings valid",
            "refused",
            f"{len(problems)} problem(s)",
            f"{first['role']}: {first['message']}",
            "Fix the label mapping; miStudio would refuse this registration after the Hub push.",
            "miStudio ProbeDatasetCreate validators",
            {"problems": problems},
        )
    return CheckOutcome(
        "D-2",
        "Label mappings valid",
        "green",
        None,
        "Every label value in every split is mapped, with both classes where miStudio needs them.",
        None,
        "miStudio ProbeDatasetCreate validators",
    )


def _d3(inputs: CheckInputs) -> CheckOutcome:
    facts = inputs.warnings
    excluded = [dict(x) for x in facts.excluded]
    said = copy.excluded_from_audit(excluded)
    if facts.warnings:
        w = facts.warnings[0]
        figure = w.get("figure")
        return CheckOutcome(
            "D-3",
            "Shortcut audit",
            "refused",
            None if figure is None else f"{float(figure):.3f}",
            copy.shortcut_warning(w) + (f" {said}" if said else ""),
            "Balance the training rows (feature 004), or set this dataset's warning level "
            "in feature 004 and record why (C4). If the label was computed from this column, "
            "declare it in the role's label source columns.",
            "R-03.19 (feature 004)",
            {"warnings": [dict(x) for x in facts.warnings], "excluded": excluded},
        )
    if facts.invalid:
        return CheckOutcome(
            "D-3",
            "Shortcut audit",
            "refused",
            None,
            "The shortcut audit could not be trusted for: "
            + ", ".join(facts.invalid)
            + "."
            + (f" {said}" if said else ""),
            "Read the shortcut audit in Version detail and fix what made its control fail.",
            "R-03.19 (feature 004)",
            {"invalid": list(facts.invalid), "excluded": excluded},
        )
    return CheckOutcome(
        "D-3",
        "Shortcut audit",
        "green",
        None,
        "No metadata column predicts the label above the warning level."
        + (f" {said}" if said else ""),
        None,
        "R-03.19 (feature 004)",
        {"excluded": excluded} if excluded else {},
    )


def _d4(inputs: CheckInputs) -> CheckOutcome:
    facts = inputs.leakage
    if facts.mined:
        return CheckOutcome(
            "D-4",
            "No leakage across roles",
            "refused",
            f"{len(facts.mined)} role(s) mined from evaluation data",
            "Mined from evaluation data: "
            + ", ".join(
                f"{role} was mined from {source}" for role, source in sorted(facts.mined.items())
            )
            + ". Rows chosen from a probe's errors on an evaluation set overlap that set.",
            "Mine from a version that is not an evaluation role, or remove the evaluation role "
            "the rows came from.",
            "R-03.18 (FR-009.59)",
            {"mined": dict(facts.mined), "mined_elsewhere": list(facts.mined_elsewhere)},
        )
    crossing = {k: int(v) for k, v in facts.crossing.items() if int(v) > 0}
    if crossing:
        total = sum(crossing.values())
        return CheckOutcome(
            "D-4",
            "No leakage across roles",
            "refused",
            f"{total:,} pair(s)",
            "Near-duplicate rows cross roles: "
            + ", ".join(f"{k.replace('|', ' and ')} ({n:,})" for k, n in sorted(crossing.items()))
            + ". The evaluation would measure memory, not the concept.",
            "Call dataworks_plan_leakage_repair (POST /api/v1/detector-sets/{id}/leakage-repair/"
            "plan): per role that gives rows up it returns a drop_leakage_pairs recipe, keeping "
            "evaluation roles whole. Save and build it, bind the new version, check again.",
            "R-03.18 (feature 004)",
            {"crossing": crossing},
        )
    return CheckOutcome(
        "D-4",
        "No leakage across roles",
        "green",
        "0 pairs",
        "No near-duplicate pair crosses two roles.",
        None,
        "R-03.18 (feature 004)",
    )


def _d5(inputs: CheckInputs) -> CheckOutcome:
    ov = inputs.overlap
    if ov is None:
        return CheckOutcome(
            "D-5",
            "Calibration length matches",
            "note",
            None,
            "The length overlap could not be computed: the set needs calibration negatives and a "
            "monitored text.",
            "Bind calibration negatives and name the monitored text.",
            "R-03.49",
        )
    figure = (
        f"{ov.figure:.1%} overlap (calibration in monitored range {ov.cal_in_ref:.1%}, "
        f"monitored in calibration range {ov.ref_in_cal:.1%})"
    )
    details = {
        "figure": ov.figure,
        "cal_in_ref": ov.cal_in_ref,
        "ref_in_cal": ov.ref_in_cal,
        "ref_range": list(ov.ref_range),
        "cal_range": list(ov.cal_range),
        "tolerance": inputs.overlap_min,
    }
    if inputs.overlap_min is None:
        return CheckOutcome(
            "D-5",
            "Calibration length matches",
            "note",
            figure,
            "No overlap tolerance is configured, so the figures are shown without a verdict.",
            "Read the two length profiles before sending.",
            "R-03.49; T-44",
            details,
        )
    if ov.figure < inputs.overlap_min:
        return CheckOutcome(
            "D-5",
            "Calibration length matches",
            "note",
            figure,
            copy.length_mismatch(ov, inputs.overlap_min),
            "Use calibration negatives the length of the text the probe will monitor, or send "
            "with this note recorded.",
            "R-03.49; T-44",
            details,
        )
    return CheckOutcome(
        "D-5",
        "Calibration length matches",
        "green",
        figure,
        "The calibration negatives are the length of the monitored text.",
        None,
        "R-03.49; T-44",
        details,
    )


def _d6(inputs: CheckInputs) -> CheckOutcome:
    synthetic = {k: int(v) for k, v in inputs.synthetic_rows.items() if int(v) > 0}
    proven = {k: dict(inputs.proven_splits[k]) for k in synthetic if k in inputs.proven_splits}
    if synthetic and len(proven) == len(synthetic):
        # 017 FR-017.4: every synthetic evaluation row sits in a value-directed held-out split
        # whose disjointness proof passed (ENH-006 D10).
        named = "; ".join(
            f"{k}: split {p['split']!r} held out by "
            + ", ".join(f"{c} in {sorted(v)}" for c, v in dict(p["held_out_values"]).items())
            for k, p in sorted(proven.items())
        )
        return CheckOutcome(
            "D-6",
            "Held-out roles not expanded",
            "note",
            ", ".join(f"{k}: {n:,} synthetic rows" for k, n in sorted(synthetic.items())),
            f"An evaluation role holds synthetic rows inside a value-directed held-out split whose "
            f"values the build proved absent from training ({named}); allowed under {CITATION}.",
            None,
            f"{CITATION}; FR-017.4",
            {"synthetic_rows": synthetic, "proofs": proven},
        )
    if synthetic:
        return CheckOutcome(
            "D-6",
            "Held-out roles not expanded",
            "note",
            ", ".join(f"{k}: {n:,} synthetic rows" for k, n in sorted(synthetic.items())),
            "An evaluation role's version contains synthetic rows; a held-out set is never "
            "expanded (R-03.38).",
            "Bind an evaluation version built from source rows only.",
            "R-03.38; 002 C-002.9",
            {"synthetic_rows": synthetic},
        )
    return CheckOutcome(
        "D-6",
        "Held-out roles not expanded",
        "green",
        None,
        "No evaluation role contains synthetic rows.",
        None,
        "R-03.38; 002 C-002.9",
    )


_REFUSING_VERDICTS = {"none", "invalid", "insufficient"}


def _d7(inputs: CheckInputs) -> CheckOutcome:
    statuses = [
        {
            "fingerprint": s.fingerprint,
            "name": s.name,
            "roles": list(s.roles),
            "verdict": s.verdict,
            "record_id": s.record_id,
            "auroc": dict(s.auroc) if s.auroc else None,
        }
        for s in inputs.labelers
    ]
    details = {"labelers": statuses, "no_record_needed": list(inputs.no_record_needed)}
    refusing = [s for s in inputs.labelers if s.verdict in _REFUSING_VERDICTS]
    if refusing:
        s = refusing[0]
        return CheckOutcome(
            "D-7",
            "Labelers have calibration records",
            "refused",
            None,
            copy.calibration_refusal(s.name, s.verdict, s.roles),
            "Compute a calibration record for this labeler in Calibration, then check again.",
            "BRD-03 section 8; P-02; 006 FR-006.20",
            details,
        )
    failing = [s for s in inputs.labelers if s.verdict == "fails"]
    if failing:
        s = failing[0]
        auroc = s.auroc or {}
        figure = (
            f"AUROC {float(auroc['value']):.3f} [{float(auroc['ci_low']):.3f}, "
            f"{float(auroc['ci_high']):.3f}], n = {int(auroc['n']):,}"
            if auroc
            else None
        )
        return CheckOutcome(
            "D-7",
            "Labelers have calibration records",
            "note",
            figure,
            f"Labeler {s.name} fails its calibration gate; its labels are used by "
            + ", ".join(s.roles)
            + ".",
            "Read the calibration record before sending; the note is written onto the send.",
            "BRD-03 section 8; P-02; 006 FR-006.20",
            details,
        )
    return CheckOutcome(
        "D-7",
        "Labelers have calibration records",
        "green",
        None,
        (
            "Every labeler whose labels a role uses has a passing calibration record."
            if inputs.labelers
            else "No role uses model labels; human or source labels need no record."
        ),
        None,
        "BRD-03 section 8; P-02; 006 FR-006.20",
        details,
    )


def _d8(inputs: CheckInputs) -> CheckOutcome:
    basis = inputs.negatives_basis or {}
    if basis.get("kind") == "human_labelled":
        values = ", ".join(repr(str(v)) for v in basis.get("negative_values") or [])
        return CheckOutcome(
            "D-8",
            "Calibration negatives basis",
            "green",
            None,
            f"The calibration negatives are human-labelled: rows whose "
            f"{basis.get('label_column')!r} is {values}, labelled by {basis.get('labelled_by')} "
            f"({basis.get('rule')}).",
            None,
            "FR-009.6",
            {"basis": dict(basis)},
        )
    if basis.get("kind") == "assumed_negative":
        return CheckOutcome(
            "D-8",
            "Calibration negatives basis",
            "note",
            None,
            "The calibration negatives are assumed negative: an unlabeled corpus mapped wholly to "
            "negative. miStudio accepts this; read it as a caveat.",
            None,
            "FR-009.6",
            {"basis": dict(basis)},
        )
    return CheckOutcome(
        "D-8",
        "Calibration negatives basis",
        "green",
        None,
        (
            "The calibration negatives were kept because a labeler scored them negative."
            if basis
            else "No calibration negatives role is bound yet."
        ),
        None,
        "FR-009.6",
        {"basis": dict(basis)} if basis else {},
    )


def wording_warns(f: WordingFacts) -> bool:
    """D55. With any computable reference: warn only when the role exceeds EVERY reference by more
    than the margin at equal size (the most lenient reading: a reference as separable as the role
    means the wording is not unusual). Without one: warn at or above the absolute bound."""
    if f.auroc is None:
        return False
    if f.comparisons:
        return all(c.auroc > c.reference_auroc + WORDING_MARGIN for c in f.comparisons)
    return f.auroc >= WORDING_ABSOLUTE


def _d9(inputs: CheckInputs) -> CheckOutcome:
    title = "Wording alone"
    source = "015 FR-015.15; ENH-005 D55"
    facts = list(inputs.wording)
    details: dict[str, Any] = {"roles": [asdict(f) for f in facts]}
    if not facts:
        return CheckOutcome(
            "D-9",
            title,
            "note",
            None,
            copy.wording_skipped(),
            None,
            source,
            details,
        )
    warned = [f for f in facts if wording_warns(f)]
    missing = [f for f in facts if f.auroc is None]
    figure = "; ".join(
        f"{f.name}: {f.auroc:.3f} (n={f.n:,})" if f.auroc is not None else f"{f.name}: not computed"
        for f in facts
    )
    details["warned"] = [f.name for f in warned]
    if warned or missing:
        return CheckOutcome(
            "D-9",
            title,
            "note",
            figure,
            copy.wording_note(warned, missing, WORDING_MARGIN, WORDING_ABSOLUTE),
            copy.wording_next_step() if warned else None,
            source,
            details,
        )
    return CheckOutcome(
        "D-9", title, "green", figure, copy.wording_green(facts), None, source, details
    )


def _d11(inputs: CheckInputs) -> CheckOutcome:
    """017 FR-017.8: a kept role from an earlier version against the new training role.

    The cross-version disjointness proof: no row of a kept role may be a training row (row keys;
    D42, D45), and a value-directed split's directing values and groups must be absent from the
    training role's rows. Refused, naming the role, the value or rows, and both versions."""
    title = "Kept evaluation disjoint from new training"
    source = "FR-017.8; ENH-006 D10, D42, D45"
    facts = list(inputs.cross_version)
    details: dict[str, Any] = {
        "checked": [
            {
                "role": f.role,
                "version_id": f.version_id,
                "training_version_id": f.training_version_id,
                "columns": list(f.columns),
                "intersection": [
                    {"column": c, "value": v, "training_rows": n} for c, v, n in f.intersection
                ],
                "shared_groups": dict(f.shared_groups),
                "missing_columns": list(f.missing_columns),
                "shared_rows": int(f.shared_rows),
                "kind": f.kind,
            }
            for f in facts
        ]
    }
    failing = [f for f in facts if not f.disjoint]
    if failing:
        f = failing[0]
        if f.shared_rows:
            what = (
                f"{int(f.shared_rows):,} row(s) are training rows of {f.training_version_label} "
                "too (same row key)"
            )
            details.update(shared_rows=int(f.shared_rows))
        elif f.missing_columns:
            what = (
                f"proof cannot be made: the rows lack column(s) "
                f"{', '.join(repr(c) for c in f.missing_columns)}, so a held-out value or group "
                "there would go unseen"
            )
            details.update(missing_columns=list(f.missing_columns))
        elif f.intersection:
            column, value, n = f.intersection[0]
            what = (
                f"held-out value {value!r} of {column!r} is in {n:,} training row(s) of "
                f"{f.training_version_label}"
            )
            details.update(column=column, value=value, count=n)
        else:
            group, n = next((g, n) for g, n in f.shared_groups.items() if int(n))
            what = f"{n:,} {group} group(s) are in both"
            details.update(group_column=group, count=n)
        details.update(
            code="cross_version_not_disjoint",
            role=f.role,
            versions=[f.version_id, f.training_version_id],
            next={
                "tool": "dataworks_update_detector_set",
                "args": {},
                "why": "bind a training version whose rows hold none of the kept role's "
                "held-out values or rows, or rebuild the kept role",
            },
        )
        return CheckOutcome(
            "D-11",
            title,
            "refused",
            f"{len(failing)} of {len(facts)} kept role(s)",
            f"{f.role} keeps {f.version_label}, but its {what} ({f.version_label} vs "
            f"{f.training_version_label}): the kept role is not disjoint from the new "
            "training rows.",
            "Bind a training version built without those values or rows, or rebuild the kept role "
            "against the new training version.",
            source,
            details,
        )
    if not facts:
        return CheckOutcome(
            "D-11",
            title,
            "green",
            None,
            "No evaluation or calibration negatives role is kept from another version than "
            "training's.",
            None,
            source,
            details,
        )
    return CheckOutcome(
        "D-11",
        title,
        "green",
        f"{len(facts)} kept role(s)",
        "No kept role's rows are training rows, and every value-directed kept role's held-out "
        "values and groups are absent from the new training rows.",
        None,
        source,
        details,
    )


def evaluate(inputs: CheckInputs) -> list[CheckOutcome]:
    """D-1 to D-9, then D-11 (017), in order. D-9 is never ``refused`` (it never blocks a send)."""
    return [
        _d1(inputs),
        _d2(inputs),
        _d3(inputs),
        _d4(inputs),
        _d5(inputs),
        _d6(inputs),
        _d7(inputs),
        _d8(inputs),
        _d9(inputs),
        _d11(inputs),
    ]


def send_allowed(outcomes: Sequence[CheckOutcome]) -> bool:
    """A send may start only when no check refuses."""
    return not any(o.outcome == "refused" for o in outcomes)


def first_refusal(outcomes: Sequence[CheckOutcome]) -> CheckOutcome | None:
    return next((o for o in outcomes if o.outcome == "refused"), None)
