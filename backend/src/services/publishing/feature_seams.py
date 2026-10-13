"""The ONE seam between feature 008 and features not built yet (004, 005, 006).

Feature 008 calls other features' public functions and never recomputes their answers (Stage 3,
2026-10-06). On 2026-10-07 none of the three exists in this repository; the search was
``ls backend/src/services/curation backend/src/services/review backend/src/services/calibration
backend/src/services/labeling`` and ``grep -rn "def check_leakage\\|def evaluate_warnings"
backend/src`` (nothing). So each call goes through this module, which:

- imports the owner's module by the path its FTDD names (004 FTDD section 5.4
  ``services/curation/api.py``; 006 FTID ``services/review/effective_label.py``,
  ``services/review/audit_service.py``, ``services/calibration/status_service.py``; 005 labeler
  identity);
- reports :data:`NOT_CHECKED` when that module does not exist yet.

**``not_checked`` is never a pass.** ``checks.py`` turns it into an AMBER outcome, so a public push
is refused until the owner lands; a missing calibration record is ``none_recorded`` and also
amber (P-02). A ``ModuleNotFoundError`` raised from INSIDE an owner's module (a broken import) is
not swallowed: only the absence of the named module itself counts as "not built yet".

For 004 to fill (recorded in the hand-off): the adapter functions below read
``LeakageResult.{exact_pairs, near_pairs, group_pairs}`` and ``WarningEvaluation.{warnings,
invalid}`` as 004 FTDD section 4.2 and 5.4 name them, and ``TrlValidationResult.{valid,
failures}``, whose field names 004 FTDD section 4.2 does not spell out — 004 confirms or edits the
two lines in :func:`_trl_finding`. The call signatures follow 004's FTDD (``inputs`` first,
``session=`` keyword), which differs from 008 FTDD's shorthand ``evaluate_warnings(version)``;
the owner's interface wins.
"""

from __future__ import annotations

import importlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any, Literal

CHECKED: Literal["checked"] = "checked"
NOT_CHECKED: Literal["not_checked"] = "not_checked"
SeamStatus = Literal["checked", "not_checked"]

#: Module paths, relative to ``src.services``, exactly as the owners' FTDD/FTID name them.
CURATION_API = "curation.api"
EFFECTIVE_LABEL = "review.effective_label"
AUDIT_SERVICE = "review.audit_service"
CALIBRATION_STATUS = "calibration.status_service"
LABELER_IDENTITY = "labeling.identity"
GENERATION_PROVENANCE = "generation.provenance"

_SERVICES = "src.services"


def load_owner(relative: str) -> ModuleType | None:
    """The owner's module, or None when it has not been built. Any other import error raises."""
    name = f"{_SERVICES}.{relative}"
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if name == missing or name.startswith(missing + "."):
            return None
        raise


@dataclass(frozen=True)
class LeakageFinding:
    status: SeamStatus
    crossing_pairs: int | None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WarningFinding:
    status: SeamStatus
    warnings: list[dict[str, Any]] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TrlFinding:
    status: SeamStatus
    valid: bool | None
    failures: list[Any] = field(default_factory=list)


@dataclass(frozen=True)
class AuditFinding:
    status: SeamStatus
    state: str | None


@dataclass(frozen=True)
class CalibrationFinding:
    """006's ``CalibrationStatus`` for one labeler, or ``none_recorded`` when 006 is absent."""

    status: SeamStatus
    recorded: bool
    record_id: str | None = None
    verdict: str | None = None
    rule: str | None = None
    auroc: dict[str, Any] | None = None
    calibration_set: dict[str, Any] | None = None


@dataclass(frozen=True)
class LabelerFinding:
    status: SeamStatus
    labelers: list[dict[str, Any]] = field(default_factory=list)
    model_ids: list[str] = field(default_factory=list)


# --- 004 ------------------------------------------------------------------------------------


def _dump(value: Any) -> Any:
    dump = getattr(value, "model_dump", None)
    return dump(mode="json") if callable(dump) else value


def check_leakage(
    inputs: Sequence[tuple[str, str]],
    *,
    held_out: Sequence[str],
    group_column: str | None,
    session: Any,
) -> LeakageFinding:
    """C-3 through 004's ``check_leakage``; counts pairs that cross into a held-out split."""
    module = load_owner(CURATION_API)
    if module is None:
        return LeakageFinding(NOT_CHECKED, None, {"owner": "004", "module": CURATION_API})
    result = module.check_leakage(inputs, group_column=group_column, session=session)
    crossing = 0
    counts: dict[str, Any] = {}
    for kind in ("exact_pairs", "near_pairs", "group_pairs"):
        by_pair: dict[str, int] = dict(getattr(result, kind))
        counts[kind] = by_pair
        for pair, n in by_pair.items():
            if any(name in str(pair) for name in held_out) and int(n) > 0:
                crossing += int(n)
    return LeakageFinding(CHECKED, crossing, counts)


def evaluate_warnings(
    inputs: Sequence[tuple[str, str]], *, label_column: str | None, session: Any
) -> WarningFinding:
    """C-7 through 004's ``evaluate_warnings`` (never a local recomputation)."""
    module = load_owner(CURATION_API)
    if module is None:
        return WarningFinding(NOT_CHECKED)
    result = module.evaluate_warnings(inputs, label_column=label_column, session=session)
    return WarningFinding(
        CHECKED,
        [dict(_dump(w)) for w in result.warnings],
        [str(c) for c in result.invalid],
    )


def _trl_finding(result: Any) -> TrlFinding:
    return TrlFinding(CHECKED, bool(result.valid), list(result.failures))


def validate_trl(version_id: str, target_type: str, *, session: Any) -> TrlFinding:
    """FR-008.26 through 004's validator in check mode."""
    module = load_owner(CURATION_API)
    if module is None:
        return TrlFinding(NOT_CHECKED, None)
    return _trl_finding(module.validate_trl(version_id, target_type, session=session))


# --- 006 ------------------------------------------------------------------------------------


def audit_status(version_id: str, *, session: Any) -> AuditFinding:
    """C-5: 006's audit status for the version."""
    module = load_owner(AUDIT_SERVICE)
    if module is None:
        return AuditFinding(NOT_CHECKED, None)
    status = module.status(version_id, session=session)
    return AuditFinding(CHECKED, str(status.state))


def calibration_status(fingerprint: str, *, session: Any) -> CalibrationFinding:
    """C-6 and M-8: 006's latest calibration status for one labeler fingerprint."""
    module = load_owner(CALIBRATION_STATUS)
    if module is None:
        return CalibrationFinding(NOT_CHECKED, recorded=False)
    status = module.latest(fingerprint=fingerprint, session=session)
    data = dict(_dump(status))
    return CalibrationFinding(
        CHECKED,
        recorded=data["status"] == "recorded",
        record_id=data["record_id"],
        verdict=data["verdict"],
        rule=data["rule"],
        auroc=data["auroc"],
        calibration_set=data["calibration_set"],
    )


def effective_labels_available() -> bool:
    """Whether 006's resolver exists. Without it no review decision can exist either."""
    return load_owner(EFFECTIVE_LABEL) is not None


def resolve_effective_labels(
    version_id: str, question_hash: str | None, label_run_id: str | None, row_keys: list[str]
) -> dict[str, dict[str, Any]] | None:
    """006's ``effective_label.resolve``; None when 006 is absent (no reviews can exist)."""
    module = load_owner(EFFECTIVE_LABEL)
    if module is None:
        return None
    resolved = module.resolve(version_id, question_hash, label_run_id, row_keys)
    return {k: dict(_dump(v)) for k, v in resolved.items()}


# --- 005 ------------------------------------------------------------------------------------


def labelers_for(run_ids: Sequence[str], *, session: Any) -> LabelerFinding:
    """Labeler identities (M-7) and the models a version's bound runs used (C-4)."""
    if not run_ids:
        return LabelerFinding(CHECKED, [], [])
    module = load_owner(LABELER_IDENTITY)
    if module is None:
        return LabelerFinding(NOT_CHECKED)
    labelers = [dict(_dump(x)) for x in module.labelers_for_runs(list(run_ids), session=session)]
    models = sorted({str(x["model_id"]) for x in labelers if x.get("model_id")})
    return LabelerFinding(CHECKED, labelers, models)


# --- 007 ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DiversityFinding:
    status: SeamStatus
    verdict: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def generators_for(run_ids: Sequence[str], *, session: Any) -> LabelerFinding:
    """Generator identities of the bound generation runs (C-4, the card; P-14)."""
    if not run_ids:
        return LabelerFinding(CHECKED, [], [])
    module = load_owner(GENERATION_PROVENANCE)
    if module is None:
        return LabelerFinding(NOT_CHECKED)
    generators = list(module.generators_for_runs(list(run_ids), session=session))
    models = sorted({str(g["model_id"]) for g in generators if g.get("model_id")})
    return LabelerFinding(CHECKED, generators, models)


def diversity_for(version_id: str, *, session: Any) -> DiversityFinding:
    """007's newest diversity verdict for the version (``falls`` → caveat, never a refusal)."""
    module = load_owner(GENERATION_PROVENANCE)
    if module is None:
        return DiversityFinding(NOT_CHECKED)
    found = module.diversity_finding(version_id, session=session)
    if found is None:
        return DiversityFinding(CHECKED, None, {})
    return DiversityFinding(CHECKED, str(found["verdict"]), dict(found))
