"""Checks C-1 to C-7, notes, and whether a push may go ahead (FR-008.11–008.16, 008.61, 008.62).

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
    check: str  # "C-1".."C-7", or "N-<code>" for a note
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


def _g(check: str, reason: str, **evidence: Any) -> CheckOutcome:
    return CheckOutcome(check, Outcome.GREEN, reason, "Nothing to do.", evidence)


def _a(check: str, reason: str, next_step: str, **evidence: Any) -> CheckOutcome:
    return CheckOutcome(check, Outcome.AMBER, reason, next_step, evidence)


def _not_checked(check: str, what: str, owner: str) -> CheckOutcome:
    return _a(
        check,
        f"{what} has not been checked: feature {owner} is not installed in this build, and an "
        "unchecked rule counts as amber.",
        f"Publish privately, or wait for feature {owner} to land and run checks again.",
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
        *labeler_notes(inp),
        *diversity_notes(inp),
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
