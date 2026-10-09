"""Feature 004's internal interface for features 007, 008 and 009 (FTDD 004 §5.4).

``evaluate_warnings`` and ``check_leakage`` look up a stored report first and otherwise compute it
INLINE in the caller's process, so their answer is never "pending" (FTDD §2.2 step 5). Warnings are
evaluated against the level in force NOW and are never stored (P-19, FR-004.34). Inputs may be
``ReportInput``s, ``(version_id, split)`` tuples (feature 008's shape), ``(version_id, split, role)``
tuples (feature 009's cross-role checks), dicts or version ids.

The session argument is the CALLER's and is used for reads only; report rows are written through
the report service's own session (``report_service``).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from ...core.config import get_settings
from ...models.enums import VersionState
from ...models.version import Version
from . import label_columns, level_service, report_service
from .codes import ReportInput, as_inputs
from .errors import CurationError
from .level_service import EffectiveLevel
from .shortcut_rules import decide_warning

logger = logging.getLogger(__name__)

SYSTEM_ACTOR = "midataworks"


@dataclass(frozen=True)
class EvaluatedWarning:
    """008's Caveat M-13 detail ``{column, figure, chance, level, level_source, sample}`` plus the
    control (FTDD §4.2)."""

    version_id: str
    column: str
    figure: float
    chance: float
    control_mean: float
    margin_pp: float
    level_source: str
    level_set_by: str | None
    n_rows: int
    sample: bool
    message: str

    def model_dump(self, mode: str = "json") -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "column": self.column,
            "figure": self.figure,
            "chance": self.chance,
            "control_mean": self.control_mean,
            "level": self.margin_pp,
            "margin_pp": self.margin_pp,
            "level_source": self.level_source,
            "level_set_by": self.level_set_by,
            "n_rows": self.n_rows,
            "sample": self.sample,
            "message": self.message,
        }


@dataclass(frozen=True)
class WarningEvaluation:
    warnings: list[EvaluatedWarning]
    audited: list[str]
    #: Columns whose control failed: no warning can be raised from them (FR-004.31).
    invalid: list[str]
    level: EffectiveLevel
    report_id: str | None
    label_column: str | None
    #: ``audited`` or ``no_label_column``: a version with no label has nothing to predict.
    status: str = "audited"
    insufficient: list[str] = field(default_factory=list)
    #: Columns the audit did not score because they ARE the label's provenance: ``label_derived``
    #: (a labeler wrote them beside the label) and ``label_source`` (the caller declared the label
    #: was computed from them). Reported so an exclusion is never silent.
    excluded: list[dict[str, Any]] = field(default_factory=list)


#: The exclusion reasons ``WarningEvaluation.excluded`` reports (the others are structural:
#: the label itself, content and system columns).
PROVENANCE_REASONS = ("label_derived", "label_source", "pair_construction")


def require_version(session: Session, version_id: str) -> Version:
    try:
        key = str(uuid.UUID(str(version_id)))
    except ValueError:
        raise CurationError(
            "version_not_found", f"{version_id!r} is not a valid version id."
        ) from None
    version = session.get(Version, key)
    if version is None:
        raise CurationError("version_not_found", f"No version {version_id}.")
    if version.state == VersionState.DELETED:
        raise CurationError(
            "version_deleted",
            f"Version {version_id} was deleted; its rows are gone, so nothing can be computed.",
            {"version_id": version_id},
        )
    return version


def audit_params(
    label_column: str | None, label_sources: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """The stored audit's parameters. ``label_sources`` is added ONLY when non-empty, so every
    report computed before it existed keeps its parameter hash and is still found."""
    s = get_settings()
    params: dict[str, Any] = {
        "label_column": label_column,
        "folds": s.curation_audit_folds,
        "control_runs": s.curation_control_runs,
        "tolerance_pp": float(s.curation_control_tolerance_pp),
    }
    if label_sources:
        params["label_sources"] = {str(k): str(v) for k, v in sorted(label_sources.items())}
    return params


def warning_message(column: str, column_result: dict[str, Any], figure: float) -> str:
    """FR-004.35: column, figure, chance, scale, sample, and what to do next."""
    from .warning_copy import warning_copy

    return warning_copy(column, column_result, figure)


def evaluate_audit(
    version_id: str, result: dict[str, Any], level: EffectiveLevel, *, sample: bool = False
) -> tuple[list[EvaluatedWarning], list[str], list[str]]:
    """Apply the level in force to stored figures (FR-004.34). Pure over its arguments."""
    warnings: list[EvaluatedWarning] = []
    audited: list[str] = []
    invalid: list[str] = []
    for column in result.get("columns", []):
        audited.append(column["column"])
        if not column["valid"]:
            invalid.append(column["column"])
        if decide_warning(
            column["figure"],
            column["chance"],
            column["control_mean"],
            level.margin_pp,
            column["valid"],
        ):
            warnings.append(
                EvaluatedWarning(
                    version_id=version_id,
                    column=column["column"],
                    figure=column["figure"],
                    chance=column["chance"],
                    control_mean=column["control_mean"],
                    margin_pp=level.margin_pp,
                    level_source=level.source,
                    level_set_by=level.set_by,
                    n_rows=column["n_rows"],
                    sample=sample,
                    message=warning_message(column["column"], column, column["figure"]),
                )
            )
    return warnings, audited, invalid


def _audit(
    session: Session,
    inputs: list[ReportInput],
    label_column: str | None,
    label_sources: Mapping[str, str] | None = None,
) -> tuple[Any, str | None]:
    from . import audit_service

    version = require_version(session, inputs[0].version_id)
    labels = label_columns.resolve(session, version.id, label_column)
    if labels.label is None:
        return None, None
    params = audit_params(labels.label, label_sources)
    if labels.construction:
        # a new parameter only for a pair-construction label, so every other stored audit keeps
        # its hash, while one computed before the exclusion existed is not reused
        params["pair_construction"] = sorted(labels.construction)
    _, report = report_service.find_or_run_inline(
        "shortcut_audit",
        inputs,
        params,
        int(version.seed),
        audit_service.compute_audit,
        started_by=SYSTEM_ACTOR,
        origin="operator",
    )
    return report, labels.label


def evaluate_warnings(
    inputs: Sequence[Any],
    *,
    label_column: str | None = None,
    label_sources: Mapping[str, str] | None = None,
    session: Session,
) -> WarningEvaluation:
    """The evaluated shortcut warnings for 008's C-7, 009's D-3 and Version detail.

    ``label_sources``: column -> who declared it, for columns the label was computed from (009's
    ``label_source_columns``). They are excluded from the audit and reported in ``excluded``.
    """
    items = as_inputs(inputs)
    if not items:
        raise CurationError("version_not_found", "evaluate_warnings needs at least one input.")
    version = require_version(session, items[0].version_id)
    level = level_service.effective_level(session, version.dataset_id)
    from .audit_service import AuditRefusal

    try:
        report, label = _audit(session, items, label_column, label_sources)
    except AuditRefusal as exc:
        if exc.code == "single_class_label":  # nothing to predict: no column can be a shortcut
            return WarningEvaluation([], [], [], level, None, label_column, status=exc.code)
        # Too few rows to score held out: unknown, so never a silent pass (008 reads it amber).
        return WarningEvaluation(
            [], [], [f"(audit {exc.code})"], level, None, label_column, status=exc.code
        )
    if report is None:
        return WarningEvaluation([], [], [], level, None, None, status="no_label_column")
    warnings, audited, invalid = evaluate_audit(version.id, report.result, level)
    excluded = [
        dict(e)
        for e in report.result.get("excluded_columns", [])
        if e.get("reason") in PROVENANCE_REASONS
    ]
    for w in warnings:
        logger.info(
            "shortcut warning version=%s column=%s figure=%.4f level=%.2f source=%s",
            version.id,
            w.column,
            w.figure,
            w.margin_pp,
            w.level_source,
        )
    return WarningEvaluation(warnings, audited, invalid, level, report.id, label, excluded=excluded)


def check_leakage(
    inputs: Sequence[Any],
    *,
    group_column: str | None = None,
    group_columns: dict[str, str | None] | None = None,
    threshold: float | None = None,
    session: Session,
) -> Any:
    """The leakage result for 008's C-3 and 009's D-4, stored or computed inline (FR-004.21)."""
    from . import leakage_service

    items = as_inputs(inputs)
    if not items:
        raise CurationError("version_not_found", "check_leakage needs at least one input.")
    version = require_version(session, items[0].version_id)
    params = leakage_service.leakage_params(group_column, threshold, group_columns)
    try:
        _, report = report_service.find_or_run_inline(
            "leakage",
            items,
            params,
            int(version.seed),
            leakage_service.compute_leakage,
            started_by=SYSTEM_ACTOR,
            origin="operator",
        )
    except CurationError as exc:
        if exc.code != "no_splits":
            raise
        return leakage_service.LeakageResult(inputs=[i.as_dict() for i in items])
    return leakage_service.LeakageResult.from_report(report)


def validate_trl(version_id: str, target_type: str, *, session: Session) -> Any:
    """The TRL validator in check mode for 008's export (FR-004.23, FR-008.26)."""
    from . import trl_service

    version = require_version(session, str(version_id))
    return trl_service.check_version(version, target_type)


def effective_level(dataset_id: str, *, session: Session) -> EffectiveLevel:
    return level_service.effective_level(session, str(dataset_id))


def assign_to_clusters(report_id: str, rows: Any) -> Any:
    """Feature 007's re-use of a clustering without refitting (FR-004.51)."""
    from .cluster_service import assign_to_clusters as assign

    return assign(report_id, rows)


__all__ = [
    "assign_to_clusters",
    "EvaluatedWarning",
    "WarningEvaluation",
    "effective_level",
    "check_leakage",
    "evaluate_warnings",
    "validate_trl",
]
