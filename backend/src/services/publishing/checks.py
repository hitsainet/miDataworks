"""Checks C-1 to C-8, notes, and whether a push may go ahead (FR-008.11–008.16, 008.61, 008.62).

C-8 (feature 016, FR-016.28; HANDOFF_PLAN D-01: 016 owns C-8, 017 takes C-9): every rule set the
version carries holds only public values (``rules/public_values``, the one authority: per case and
every string). ``N-rule_source_overlap`` is a note (refuses nothing; OI-3 open for the operator).

Four outcomes (FR-008.62):

- **green**: passes;
- **note**: shown and written into the card; refuses nothing;
- **amber**: refuses a PUBLIC push (R-03.43, X-02), permits a private one with a caveat;
- **refused**: the push cannot succeed (C-2 only), private included (FR-008.13).

:func:`evaluate_checks` is pure. :func:`gather_curation_findings` is the one place 008 calls
feature 004 — ``check_leakage`` for C-3 and ``evaluate_warnings`` for C-7, through
``feature_seams`` — and an AST test pins both calls. A check whose owner has not been built
reports ``not_checked`` and becomes AMBER here: a missing check is never a pass.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Literal

from . import feature_seams
from .feature_seams import (
    NOT_CHECKED,
    AuditFinding,
    CalibrationFinding,
    DiversityFinding,
    LabelerFinding,
    LeakageFinding,
    WarningFinding,
)
from .licence_table import LicenceClass


class Outcome(StrEnum):
    GREEN = "green"
    NOTE = "note"
    AMBER = "amber"
    REFUSED = "refused"


@dataclass(frozen=True)
class CheckOutcome:
    check: str  # "C-1".."C-9" (C-8 is 016's, C-9 017's), or "N-<code>" for a note
    outcome: Outcome
    reason: str
    next_step: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["outcome"] = self.outcome.value
        return data

    @staticmethod
    def from_dict(data: dict[str, Any]) -> CheckOutcome:
        return CheckOutcome(
            check=data["check"],
            outcome=Outcome(data["outcome"]),
            reason=data["reason"],
            next_step=data["next_step"],
            evidence=dict(data["evidence"]),
        )


TokenScope = Literal["write", "read", "missing", "invalid", "cannot_verify", "namespace_denied"]


@dataclass(frozen=True)
class SourceLicence:
    source_id: str
    name: str
    displayed: str
    current_class: LicenceClass
    built_class: LicenceClass | None  # the class the version's manifest recorded, if any


@dataclass(frozen=True)
class ModelTerms:
    model_id: str
    latest: str | None  # "permits" | "forbids" | None (no note)


@dataclass(frozen=True)
class LabelerCheck:
    fingerprint: str
    name: str
    pinned: bool
    revision_reported: bool
    calibration: CalibrationFinding


@dataclass(frozen=True)
class CarriedRuleSetFacts:
    """One rule-set version the version carries (feature 016), as C-8 and its note read it."""

    name: str
    version: int
    behaviour_key: str
    #: Public-value violations (``rules/public_values``), each naming the case or string and why.
    violations: tuple[str, ...] = ()
    #: ``provenance.source_version_id`` of a derived rule set, else None.
    derived_from: str | None = None
    #: Rows of ``derived_from`` that are also rows of this version's held-out splits.
    overlap_rows: int = 0


@dataclass(frozen=True)
class CarriedCodeRuleFacts:
    """One code rule the version carries (feature 018), as C-8 and C-9 read it."""

    name: str
    version: int
    code_sha256: str
    #: ``none``, ``private_only`` or ``forbids_redistribution`` (FR-018.41; strictest of its
    #: derived-from sources and its cases' source versions).
    visibility_floor: str
    #: Public-value violations in its cases and its document (``rules/public_values``).
    violations: tuple[str, ...] = ()
    floor_basis: tuple[Mapping[str, str], ...] = ()


@dataclass(frozen=True)
class CheckInputs:
    visibility: Literal["private", "public"]
    sources: Sequence[SourceLicence]
    token_scope: TokenScope
    held_out_splits: Sequence[str]
    leakage: LeakageFinding
    models: LabelerFinding
    model_terms: Sequence[ModelTerms]
    audit: AuditFinding
    labelers: Sequence[LabelerCheck]
    labeler_status: str  # "checked" | "not_checked"
    warnings: WarningFinding
    #: Feature 007's newest diversity verdict; ``falls`` adds the note N-diversity_falling (P-01).
    diversity: DiversityFinding | None = None
    #: Rows whose ``_dw_origin`` is ``generated`` in the version's own files (read from the rows).
    generated_rows: int = 0
    #: Generator identities of the generation runs bound anywhere in the lineage (007).
    generators: Sequence[Mapping[str, Any]] = ()
    #: 004's label-balancer steps in the lineage (FR-004.56): each adds the note N-label_balanced.
    label_balancing: Sequence[Mapping[str, Any]] = ()
    #: Feature 016: the rule sets the version carries; None = not recorded (built before 016).
    carried_rule_sets: Sequence[CarriedRuleSetFacts] | None = ()
    #: 017 FR-017.48 (0042): ``private_only`` when the version's recipe or an input declared it.
    visibility_floor: str | None = None
    #: Feature 018: the code rules the version carries; None = not recorded (built before 018).
    carried_code_rules: Sequence[CarriedCodeRuleFacts] | None = ()


def _g(check: str, reason: str, **evidence: Any) -> CheckOutcome:
    return CheckOutcome(check, Outcome.GREEN, reason, "Nothing to do.", evidence)


def _a(check: str, reason: str, next_step: str, **evidence: Any) -> CheckOutcome:
    return CheckOutcome(check, Outcome.AMBER, reason, next_step, evidence)


def _not_checked(check: str, what: str, owner: str) -> CheckOutcome:
    return _a(
        check,
        f"{what} has not been checked: feature {owner}'s check could not be loaded, which is a "
        "fault in miDataworks; an unchecked rule counts as amber.",
        "Publish privately, or run checks again; if it persists, report it.",
        not_checked=True,
        owner=owner,
    )


def c1_licences(inp: CheckInputs) -> list[CheckOutcome]:
    out: list[CheckOutcome] = []
    blocking = [s for s in inp.sources if s.current_class is not LicenceClass.PERMITS]
    if blocking:
        first = blocking[0]
        out.append(
            _a(
                "C-1",
                f"{len(blocking)} source(s) do not permit redistribution; first: {first.name} "
                f"(licence {first.displayed!r}, class {first.current_class.value}).",
                f"Record the terms of {first.name} in its source before publishing publicly, "
                "or publish privately.",
                sources=[s.source_id for s in blocking],
            )
        )
    else:
        out.append(_g("C-1", f"All {len(inp.sources)} source licences permit redistribution."))
    changed = [s for s in inp.sources if s.built_class and s.built_class is not s.current_class]
    for s in changed:
        assert s.built_class is not None
        out.append(
            CheckOutcome(
                "N-licence_changed_since_build",
                Outcome.NOTE,
                f"{s.name}'s licence class changed since this version was built: "
                f"{s.built_class.value} then, {s.current_class.value} now.",
                "The checks use today's class; the card records both.",
                {
                    "source_id": s.source_id,
                    "built": s.built_class.value,
                    "current": s.current_class.value,
                },
            )
        )
    return out


def c2_token(inp: CheckInputs) -> CheckOutcome:
    if inp.token_scope == "write":  # noqa: S105 - a scope name, not a secret
        return _g("C-2", "The stored Hugging Face token can write to this namespace.")
    reasons = {
        "missing": "No Hugging Face token is stored.",
        "invalid": "The Hub rejected the stored Hugging Face token.",
        "read": "The stored Hugging Face token is read-only.",
        "namespace_denied": "The stored token cannot write to this repository's namespace.",
        "cannot_verify": "The token's scope could not be verified, so it is treated as unable "
        "to write.",
    }
    return CheckOutcome(
        "C-2",
        Outcome.REFUSED,
        reasons[inp.token_scope],
        "Update the Hugging Face token in Settings with one that can write, then run checks again.",
        {"scope": inp.token_scope},
    )


def c3_leakage(inp: CheckInputs) -> CheckOutcome:
    if not inp.held_out_splits:
        return _g("C-3", "The version has no held-out split, so nothing can leak into one.")
    if inp.leakage.status == NOT_CHECKED:
        return _not_checked("C-3", "Near-duplicate leakage into the held-out split", "004")
    if inp.leakage.crossing_pairs:
        return _a(
            "C-3",
            f"{inp.leakage.crossing_pairs} near-duplicate pair(s) cross between train and the "
            f"held-out split {', '.join(inp.held_out_splits)}.",
            "Remove the duplicates with a dedup step before the split, then build a new version.",
            **inp.leakage.detail,
        )
    return _g("C-3", "No near-duplicate pair crosses into the held-out split.")


def _who_made_it(inp: CheckInputs) -> tuple[str, dict[str, Any]]:
    """What C-4 is about, said every time: generated rows and their generators, model labelers."""
    gen_models = sorted({str(g.get("model_id")) for g in inp.generators if g.get("model_id")})
    gen_runs = sorted({str(g.get("generation_run_id")) for g in inp.generators})
    labelers = [
        {
            "model_id": lab.get("model_id"),
            "role": lab.get("role"),
            "run_ids": list(lab.get("run_ids") or []),
        }
        for lab in inp.models.labelers
    ]
    parts = []
    if inp.generated_rows or gen_models:
        parts.append(
            f"{inp.generated_rows:,} generated row(s)"
            + (f" from {', '.join(gen_models)}" if gen_models else "")
        )
    if labelers:
        parts.append(
            "labels from "
            + ", ".join(f"{x['role']} {x['model_id'] or 'model not stated'}" for x in labelers)
        )
    evidence = {
        "generated_rows": inp.generated_rows,
        "generator_models": gen_models,
        "generation_runs": gen_runs,
        "labelers": labelers,
    }
    return "; ".join(parts), evidence


def c4_model_terms(inp: CheckInputs) -> CheckOutcome:
    if inp.models.status == NOT_CHECKED:
        return _not_checked("C-4", "Which models labelled or generated rows", "005")
    said, evidence = _who_made_it(inp)
    if inp.generated_rows and not inp.generators:
        # the rows say a model wrote them; no run anywhere in the lineage says which model
        return _a(
            "C-4",
            f"{inp.generated_rows:,} row(s) are marked generated, but no generation run is bound "
            "anywhere in this version's lineage, so the generator's terms cannot be checked.",
            "Build the version from the generation run's candidate version (which binds it), "
            "then run checks again.",
            **evidence,
        )
    if not inp.models.model_ids:
        return _g("C-4", "No model labelled or generated rows in this version or its lineage.")
    notes = {t.model_id: t.latest for t in inp.model_terms}
    missing = [m for m in inp.models.model_ids if notes.get(m) is None]
    forbids = [m for m in inp.models.model_ids if notes.get(m) == "forbids"]
    if forbids:
        return _a(
            "C-4",
            f"The latest terms note for {forbids[0]} says its terms forbid training on outputs "
            f"({said}).",
            "Publish privately, or relabel with a model whose terms permit it.",
            forbids=forbids,
            **evidence,
        )
    if missing:
        return _a(
            "C-4",
            f"No terms note is recorded for {missing[0]}"
            + (f" and {len(missing) - 1} more model(s)" if len(missing) > 1 else "")
            + f" ({said}).",
            f"Record whether {missing[0]}'s terms permit training on its outputs.",
            missing=missing,
            **evidence,
        )
    return _g(
        "C-4",
        f"All {len(inp.models.model_ids)} model(s) have a terms note that permits it ({said}).",
        **evidence,
    )


def c5_audit(inp: CheckInputs) -> CheckOutcome:
    if inp.audit.status == NOT_CHECKED:
        return _not_checked("C-5", "The stratified audit sample", "006")
    if inp.audit.state != "complete":
        return _a(
            "C-5",
            f"The audit sample for this version is {inp.audit.state or 'not drawn'}.",
            "Draw and finish the 50–100 row audit sample in Review, then run checks again.",
            state=inp.audit.state,
        )
    return _g("C-5", "A completed audit sample exists for this version.")


def c6_calibration(inp: CheckInputs) -> list[CheckOutcome]:
    if inp.labeler_status == NOT_CHECKED:
        return [_not_checked("C-6", "Which labelers produced the labels", "005")]
    if not inp.labelers:
        return [_g("C-6", "No model labeler produced labels in this version or its lineage.")]
    out: list[CheckOutcome] = []
    blocking: list[str] = []
    for labeler in inp.labelers:
        cal = labeler.calibration
        if cal.status == NOT_CHECKED or not cal.recorded:
            blocking.append(f"{labeler.name}: no calibration record")
        elif cal.verdict in ("invalid", "insufficient"):
            # P-02 reading confirmed S3-05: `insufficient` is treated as `invalid`.
            blocking.append(f"{labeler.name}: verdict {cal.verdict}")
        elif cal.verdict == "fails":
            out.append(
                CheckOutcome(
                    "N-failing_verdict",
                    Outcome.NOTE,
                    f"{labeler.name}'s calibration verdict is fails"
                    + (f" (AUROC {cal.auroc['value']:.3f})." if cal.auroc else "."),
                    "The card states the verdict and its numbers.",
                    {"fingerprint": labeler.fingerprint, "record_id": cal.record_id},
                )
            )
    if blocking:
        out.insert(
            0,
            _a(
                "C-6",
                f"{len(blocking)} labeler(s) lack a valid calibration record; first: {blocking[0]}.",
                "Record a calibration for each labeler in Calibration, then run checks again.",
                labelers=blocking,
            ),
        )
    else:
        out.insert(0, _g("C-6", f"All {len(inp.labelers)} labeler(s) have a calibration record."))
    return out


def c7_shortcuts(inp: CheckInputs) -> CheckOutcome:
    if inp.warnings.status == NOT_CHECKED:
        return _not_checked("C-7", "The shortcut audit", "004")
    if inp.warnings.warnings or inp.warnings.invalid:
        first = inp.warnings.warnings[0] if inp.warnings.warnings else None
        reason = (
            f"Column {first.get('column')!r} predicts the label at or above its warning level."
            if first
            else f"The shortcut audit could not be computed for {inp.warnings.invalid[0]!r}."
        )
        return _a(
            "C-7",
            reason,
            "Balance the version on that column or remove it, then build a new version; the "
            "per-dataset level is changed in the dataset's settings, with a reason.",
            warnings=inp.warnings.warnings,
            invalid=inp.warnings.invalid,
        )
    return _g("C-7", "No audited column predicts the label at or above its warning level.")


def labeler_notes(inp: CheckInputs) -> list[CheckOutcome]:
    out: list[CheckOutcome] = []
    for labeler in inp.labelers:
        if not labeler.pinned:
            out.append(
                CheckOutcome(
                    "N-unpinned_labeler",
                    Outcome.NOTE,
                    f"{labeler.name}'s run was recorded as unpinned (no model lease).",
                    "The card says so; nothing to do unless you need a pinned run.",
                    {"fingerprint": labeler.fingerprint},
                )
            )
        if not labeler.revision_reported:
            out.append(
                CheckOutcome(
                    "N-revision_not_reported",
                    Outcome.NOTE,
                    f"{labeler.name}'s endpoint did not report a model revision.",
                    "The card says so; nothing to do.",
                    {"fingerprint": labeler.fingerprint},
                )
            )
    return out


def diversity_notes(inp: CheckInputs) -> list[CheckOutcome]:
    """A falling diversity verdict is a NOTE, never amber and never a refusal (P-01)."""
    finding = inp.diversity
    if finding is None or finding.verdict != "falls":
        return []
    return [
        CheckOutcome(
            "N-diversity_falling",
            Outcome.NOTE,
            "The diversity report says generated rows narrowed the data: "
            + ", ".join(finding.detail.get("falling") or ["a figure"])
            + " fell below the reference.",
            "Cap the largest cluster or generate from more seed rows; nothing is refused.",
            {
                "report_id": finding.detail.get("report_id"),
                "falling": finding.detail.get("falling"),
            },
        )
    ]


def label_balance_notes(inp: CheckInputs) -> list[CheckOutcome]:
    """Rows selected by their label: a NOTE (refuses nothing), always shown (FR-004.55, 004.56)."""
    if not inp.label_balancing:
        return []
    steps = list(inp.label_balancing)
    where = ", ".join(f"v{s['version_number']} step {s['step_index']}" for s in steps)
    return [
        CheckOutcome(
            "N-label_balanced",
            Outcome.NOTE,
            f"Rows were selected by their label ({where}). {steps[0]['warning']['message']}",
            "State the balanced class prior wherever the data is used; nothing is refused.",
            {"steps": [dict(s) for s in steps]},
        )
    ]


def c8_carried_rule_sets(inp: CheckInputs) -> CheckOutcome:
    """C-8: every carried rule set holds only public values (FR-016.28); amber names the rule
    set, the case and the reason, so a public push is refused and a private one allowed."""
    code_bad = [(c, v) for c in inp.carried_code_rules or () for v in c.violations]
    if code_bad:
        # Feature 018 (FR-018.41): a carried code rule's cases and document follow the same rule.
        first_code, why_code = code_bad[0]
        return _a(
            "C-8",
            f"Code rule {first_code.name}@{first_code.version} carries a value that may not be "
            f"public: {why_code}." + (f" ({len(code_bad) - 1} more.)" if len(code_bad) > 1 else ""),
            "Publish privately, or save a version of the code rule whose cases use published "
            "test values only, re-save the rule set naming it, move the recipe and rebuild.",
            violations=[
                {"code_rule": f"{c.name}@{c.version}", "violation": v} for c, v in code_bad
            ],
        )
    carried = inp.carried_rule_sets
    if carried is None:
        return _g("C-8", "Rule sets are not recorded for this version; none are carried.")
    if not carried:
        return _g("C-8", "No rule sets carried.")
    bad = [(c, v) for c in carried for v in c.violations]
    if bad:
        first, why = bad[0]
        return _a(
            "C-8",
            f"Rule set {first.name}@{first.version} carries a value that may not be public: {why}."
            + (f" ({len(bad) - 1} more.)" if len(bad) > 1 else ""),
            "Publish privately, or save a version of the rule set whose cases use published "
            "test values only, move the recipe to it and rebuild.",
            violations=[{"rule_set": f"{c.name}@{c.version}", "violation": v} for c, v in bad],
        )
    return _g(
        "C-8",
        f"{len(carried)} rule set(s) carried; every value in them is public.",
        rule_sets=[f"{c.name}@{c.version}" for c in carried],
    )


def c9_visibility_floor(inp: CheckInputs) -> CheckOutcome:
    """C-9 (017 FR-017.48; FTID I8): a version built under a declared ``private_only`` floor is
    REFUSED for a public push (whatever an approval says), naming the declaration; any other
    version, or a private push, is green. A floor is data the recipe declares, never a behaviour
    named in code. Feature 018 (FR-018.41): a CARRIED CODE RULE whose floor is
    ``private_only`` or ``forbids_redistribution`` refuses a public push the same way, naming the
    code rule and the references that set its floor; running a rule never lowers its floor."""
    strict = [c for c in inp.carried_code_rules or () if c.visibility_floor != "none"]
    if strict and inp.visibility == "public":
        first = strict[0]
        return CheckOutcome(
            "C-9",
            Outcome.REFUSED,
            f"This version carries code rule {first.name}@{first.version}, whose visibility floor "
            f"is {first.visibility_floor} (it was derived from or tested on rows that may not be "
            "published): it may not be published publicly."
            + (f" ({len(strict) - 1} more.)" if len(strict) > 1 else ""),
            "Publish it privately, or build with a code rule derived from and tested on public "
            "sources only.",
            {
                "code": "code_rule_floor_public_refused",
                "code_rules": [
                    {
                        "code_rule": f"{c.name}@{c.version}",
                        "code_sha256": c.code_sha256,
                        "visibility_floor": c.visibility_floor,
                        "basis": [dict(b) for b in c.floor_basis],
                    }
                    for c in strict
                ],
            },
        )
    if inp.visibility_floor != "private_only":
        if strict:
            return _g(
                "C-9",
                f"Code rule {strict[0].name}@{strict[0].version} has a "
                f"{strict[0].visibility_floor} floor; a private push is allowed.",
                code_rules=[f"{c.name}@{c.version}" for c in strict],
            )
        return _g("C-9", "No visibility floor is declared for this version.")
    if inp.visibility == "public":
        return CheckOutcome(
            "C-9",
            Outcome.REFUSED,
            "This version was built under a declared visibility floor, private_only (its recipe "
            "or an input version declares it): it may not be published publicly.",
            "Publish it privately, or build from inputs and a recipe that declare no floor.",
            {"visibility_floor": "private_only", "code": "private_only_public_refused"},
        )
    return _g(
        "C-9",
        "The version declares private_only; a private push is allowed.",
        visibility_floor="private_only",
    )


def rule_source_notes(inp: CheckInputs) -> list[CheckOutcome]:
    """A derived rule set whose source shares rows with this version's held-out splits: a NOTE
    (refuses nothing; C-3 cannot see it, FTDD 016 section 7.6; whether it is amber is OI-3)."""
    out: list[CheckOutcome] = []
    for c in inp.carried_rule_sets or ():
        if c.derived_from is None or c.overlap_rows <= 0:
            continue
        out.append(
            CheckOutcome(
                "N-rule_source_overlap",
                Outcome.NOTE,
                f"Rule set {c.name}@{c.version} was derived from version {c.derived_from}, which "
                f"shares {c.overlap_rows:,} row(s) with this version's held-out splits: its rules "
                "were learned partly from rows it is now measured on.",
                "Derive the rule set from a version without the held-out rows; nothing is refused.",
                {
                    "rule_set": f"{c.name}@{c.version}",
                    "source_version_id": c.derived_from,
                    "overlap_rows": c.overlap_rows,
                },
            )
        )
    return out


def evaluate_checks(inp: CheckInputs) -> list[CheckOutcome]:
    """Every check and note, in order. Pure: no I/O."""
    return [
        *c1_licences(inp),
        c2_token(inp),
        c3_leakage(inp),
        c4_model_terms(inp),
        c5_audit(inp),
        *c6_calibration(inp),
        c7_shortcuts(inp),
        c8_carried_rule_sets(inp),
        c9_visibility_floor(inp),
        *labeler_notes(inp),
        *diversity_notes(inp),
        *label_balance_notes(inp),
        *rule_source_notes(inp),
    ]


def push_allowed(
    outcomes: Sequence[CheckOutcome], visibility: str
) -> tuple[bool, list[CheckOutcome]]:
    """Any refused outcome refuses every push; any amber refuses a public push (X-02).

    An operator's approval of an agent request does not override this: the publish job calls it
    after approval, in the worker, for every path (FR-008.12).
    """
    blocking = [
        o
        for o in outcomes
        if o.outcome is Outcome.REFUSED or (visibility == "public" and o.outcome is Outcome.AMBER)
    ]
    return (not blocking, blocking)


def gather_curation_findings(
    inputs: Sequence[tuple[str, str]],
    *,
    held_out: Sequence[str],
    group_column: str | None,
    label_column: str | None,
    session: Any,
) -> tuple[LeakageFinding, WarningFinding]:
    """C-3 and C-7 inputs, from feature 004 only (Stage 3, requested by 004)."""
    leakage = feature_seams.check_leakage(
        inputs, held_out=held_out, group_column=group_column, session=session
    )
    warnings = feature_seams.evaluate_warnings(inputs, label_column=label_column, session=session)
    return leakage, warnings
