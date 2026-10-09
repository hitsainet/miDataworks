"""Which column is the label, and which columns were derived from it (FTDD 004 §6.4; FTID §7.10).

Read from labeler MANIFESTS, never from column names (I-m):

1. Walk the version's operator steps newest first (feature 002's ``dw_version_steps`` ->
   ``dw_step_executions``). The newest step whose operator kind is ``labeler`` provides the label:
   the output column whose declared role is ``label``; else the output column named ``label``
   (feature 005's Threshold labeler writes ``label`` and ``label_probability``); else its single
   output column. Every OTHER output column of that labeler is label-derived.
2. Otherwise the caller's chosen ``label_column``.
3. Otherwise ``no_label_column``.

FTDD §6.4 says the steps live in ``dw_version_inputs``; in the code they live in
``dw_version_steps`` (inputs are sources and parent versions). The code wins (recorded).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models.step_execution import StepExecution
from ...models.version import VersionStep
from ..version_lineage import lineage_versions

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LabelColumns:
    #: The label column, or None (``no_label_column``).
    label: str | None
    #: ``labeler`` (from a step's manifest) or ``chosen`` (by the caller), or None.
    source: str | None
    #: Label-derived column -> the operator ``name@version`` that wrote it.
    derived: dict[str, str] = field(default_factory=dict)
    #: The labeler step (for its ``excluded_by_band`` events), when the label came from one.
    labeler_execution_id: str | None = None
    labeler_step_index: int | None = None
    #: Pair-construction column -> the operator ``name@version`` that wrote it (see
    #: :func:`pair_construction`). Excluded from the audit as ``pair_construction``.
    construction: dict[str, str] = field(default_factory=dict)


#: Labelers whose label is the ROLE a row plays in a constructed pair (009's minimal pairs): the
#: counterpart is, by definition, the generated row the judge verified, so every column recording
#: how the pair was built predicts that label by construction.
PAIR_CONSTRUCTION_LABELERS = ("minimal_pair_join",)
#: Operators that add generated rows; their output columns exist on generated rows only.
GENERATED_ROW_OPERATORS = ("dw_generated_rows",)
#: Audited system columns that say whether a row was generated (and so which side of a pair it is).
CONSTRUCTION_SYSTEM = ("_dw_origin", "_dw_source_id")


def lineage_steps(session: Session, version_id: str) -> list[StepExecution]:
    """Operator steps of the version and then of its ancestor versions (nearest first), each
    version's steps newest first. A re-split of a pair version keeps its rows' pair columns, so the
    step that wrote them is found in an ancestor. The walk is ``version_lineage``'s, shared with
    the publish record."""
    out: list[StepExecution] = []
    for version in lineage_versions(session, str(version_id)):
        out.extend(
            session.execute(
                select(StepExecution)
                .join(VersionStep, VersionStep.step_execution_id == StepExecution.id)
                .where(VersionStep.version_id == version.id, StepExecution.kind == "operator")
                .order_by(VersionStep.step_index.desc())
            ).scalars()
        )
    return out


def pair_construction(
    session: Session, version_id: str, label: str | None, *, registry: Any = None
) -> dict[str, str]:
    """When ``label`` is an output column of a pair-construction labeler in the version's lineage
    (read from that operator's MANIFEST, not from the column's name): every OTHER column it wrote,
    every column a generated-rows operator in the lineage wrote, and the origin and source-id
    system columns, each mapped to the operator that wrote it. Empty otherwise."""
    if label is None:
        return {}
    reg = registry if registry is not None else _registry()
    steps = lineage_steps(session, version_id)
    join_ref: str | None = None
    out: dict[str, str] = {}
    for execution in steps:
        if execution.operator_name not in PAIR_CONSTRUCTION_LABELERS:
            continue
        try:
            info = reg.get(execution.operator_name, execution.operator_version)
        except Exception as exc:  # noqa: BLE001 - an uninstalled operator cannot be read
            logger.info("pair construction skipped %s: %s", execution.operator_name, exc)
            continue
        if label not in dict(info.output_columns):
            continue
        join_ref = f"{execution.operator_name}@{execution.operator_version}"
        out.update({c: join_ref for c in dict(info.output_columns) if c != label})
        break
    if join_ref is None:
        return {}
    for execution in steps:
        if execution.operator_name not in GENERATED_ROW_OPERATORS:
            continue
        try:
            info = reg.get(execution.operator_name, execution.operator_version)
        except Exception as exc:  # noqa: BLE001
            logger.info("pair construction skipped %s: %s", execution.operator_name, exc)
            continue
        ref = f"{execution.operator_name}@{execution.operator_version}"
        for column in dict(info.output_columns):
            out.setdefault(column, ref)
    for column in CONSTRUCTION_SYSTEM:
        out.setdefault(column, join_ref)
    return out


def label_of_manifest(output_columns: dict[str, str]) -> str | None:
    """The declared label output of a labeler's manifest (``{name: role}``)."""
    by_role = [name for name, role in output_columns.items() if role == "label"]
    if len(by_role) == 1:
        return by_role[0]
    if "label" in output_columns:
        return "label"
    if len(output_columns) == 1:
        return next(iter(output_columns))
    return None


def _registry() -> Any:
    from ...operators.registry import current

    return current()


def resolve(
    session: Session,
    version_id: str,
    chosen: str | None = None,
    *,
    registry: Any = None,
) -> LabelColumns:
    """Resolve the label and the label-derived columns of a version."""
    reg = registry if registry is not None else _registry()
    steps = session.execute(
        select(VersionStep.step_index, StepExecution)
        .join(StepExecution, StepExecution.id == VersionStep.step_execution_id)
        .where(VersionStep.version_id == version_id, StepExecution.kind == "operator")
        .order_by(VersionStep.step_index.desc())
    ).all()
    derived: dict[str, str] = {}
    for step_index, execution in steps:
        try:
            info = reg.get(execution.operator_name, execution.operator_version)
        except Exception as exc:  # noqa: BLE001 - an uninstalled operator is not a readable labeler
            logger.info("label resolution skipped %s: %s", execution.operator_name, exc)
            continue
        if info.kind != "labeler":
            continue
        label = label_of_manifest(dict(info.output_columns))
        ref = f"{execution.operator_name}@{execution.operator_version}"
        for column in info.output_columns:
            if column != label:
                derived[column] = ref
        if label is not None:
            final = chosen or label
            return LabelColumns(
                label=final,
                source="chosen" if chosen and chosen != label else "labeler",
                derived={k: v for k, v in derived.items() if k != final},
                labeler_execution_id=execution.id,
                labeler_step_index=int(step_index),
                construction=pair_construction(session, version_id, final, registry=reg),
            )
    if chosen:
        return LabelColumns(
            label=chosen,
            source="chosen",
            derived={k: v for k, v in derived.items() if k != chosen},
            construction=pair_construction(session, version_id, chosen, registry=reg),
        )
    return LabelColumns(label=None, source=None, derived=derived)
