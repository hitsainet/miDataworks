"""A build's dry run: what it would count, gate and cap, without building (PII run G6, L3).

The run read ``agent_label_ledger.py`` to learn whether its ``dd_expression`` steps counted against
the 5,000-row agent labelling window (they do not: only a step that calls a model through an
endpoint role, or a probe-verdict / feature-tagging labeler, counts), and lost a build to a
``cell_balancer`` that capped EVERY cell at the single smallest cell of the whole table. This
answers both before the build, and writes nothing (no job, no ledger row, no version):

- the recipe checked against the inputs' columns (``recipe_service.validate_body``);
- the labelling steps with WHY each counts, the rows counted per the ledger's own rule
  (``version_build_service.labelling_plan``: every labelling step counts the build's input rows,
  an upper bound), the window total now, the threshold, and whether an agent would wait for
  approval (``agent_label_rows``) — the same arithmetic as ``agent_label_ledger.needs_approval``,
  read without taking its lock;
- per ``cell_balancer`` step, the cells and the global cap computed with the balancer's own
  ``cells.cell_cap_plan`` on the build's inputs, or why it cannot be computed before the build.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..core.storage import resolve_under_data_dir
from ..models.enums import InputKind
from ..models.version import Version
from . import agent_label_ledger, recipe_service
from .assembly import initial_roles
from .duck import connect, files_param, quote_ident
from .operator_port import registry
from .sources.reader import list_files
from .version_build_service import (
    get_buildable_revision,
    get_dataset,
    labelling_plan,
    resolve_inputs,
)

BALANCER = "cell_balancer"
GLOBAL_CAP_NOTE = (
    "cell_balancer caps EVERY (value x label) cell at the single smallest cell of the whole "
    "table, not per value: one small cell shrinks every other cell to its size, and an empty cell "
    "(a value with no row of some label) refuses the build. Use coarser values or exclude_values "
    "for rare ones."
)


def _why(info: Any) -> str:
    if info.endpoint_role is not None:
        return f"calls a model through the {info.endpoint_role} endpoint role"
    return f"is a {info.detector_labeler_kind} labeler (feature 009)"


async def _input_files(db: AsyncSession, inputs: list[Any]) -> list[Path]:
    paths: list[Path] = []
    for ref in inputs:
        if ref.kind == InputKind.SOURCE:
            paths += [resolve_under_data_dir(f.path) for f in await list_files(db, ref.source_id)]
        else:
            version = await db.get(Version, ref.version_id)
            if version is not None:
                paths += [resolve_under_data_dir(s["path"]) for s in version.splits]
    return paths


def _balancer(
    index: int, params: dict[str, Any], columns: set[str], paths: list[Path], seed: int
) -> dict[str, Any]:
    from .curation.binning import NULL_LABEL
    from .curation.cells import CellRefusal, cell_cap_plan

    column = str(params.get("column"))
    label = str(params.get("label_column") or "label")
    out: dict[str, Any] = {
        "step": index,
        "column": column,
        "label_column": label,
        "rule": GLOBAL_CAP_NOTE,
    }
    missing = [c for c in (column, label) if c not in columns]
    if missing:
        out.update(
            computable=False,
            reason=f"{missing} are not in the build's inputs (an earlier step writes them), so "
            "the cells can only be seen once that step has run: preview the balancer on a "
            "version that has them.",
        )
        return out
    con = connect()
    try:
        table = con.execute(
            f"SELECT {quote_ident(column, columns)}, {quote_ident(label, columns)} "  # noqa: S608
            "FROM read_parquet(?, union_by_name=true)",
            [files_param(paths)],
        ).fetchall()
    finally:
        con.close()
    values = [NULL_LABEL if v is None else str(v) for v, _ in table]
    labels = [None if lab is None else str(lab) for _, lab in table]
    if any(lab is None for lab in labels):
        out.update(
            computable=False,
            reason=f"{sum(lab is None for lab in labels)} input row(s) have no {label!r}; the "
            "balancer refuses unlabelled rows (label_has_nulls).",
        )
        return out
    try:
        plan = cell_cap_plan(
            values,
            [str(lab) for lab in labels],
            exclude_values=list(params.get("exclude_values") or []),
            seed=seed,
        )
    except CellRefusal as exc:
        out.update(computable=True, refused={"code": exc.code, "message": exc.message})
        return out
    kept = int(plan.kept_mask.sum())
    smallest = min(plan.cells.values(), key=lambda c: int(str(c["before"])))
    out.update(
        computable=True,
        exact=index == 1,
        cap=plan.cap,
        smallest_cell={"value": smallest["value"], "label": smallest["label"]},
        rows_in=len(values),
        rows_kept=kept,
        rows_dropped=len(values) - kept,
        cells=plan.cells,
        note=(
            None
            if index == 1
            else f"Computed on the build's inputs; steps 1 to {index - 1} run first and may change "
            "the rows this balancer sees."
        ),
    )
    return out


async def plan_build(db: AsyncSession, req: Any, designer_check: Any = None) -> dict[str, Any]:
    """``designer_check(validation, body, columns_seen)`` (the API's ``with_designer_rules``)
    checks every Data Designer template against the columns its step will really see: the
    inputs' columns plus earlier steps' outputs. e2e run G2: plan_build reported a recipe valid
    whose build then failed on Data Designer's depth rule (13 > 10)."""
    dataset = await get_dataset(db, req.dataset_id)
    _, revision = await get_buildable_revision(db, req.recipe_revision_id)
    inputs = await resolve_inputs(db, req.inputs)
    columns = {c["name"] for i in inputs for c in i.columns if not c["name"].startswith("_dw_")}
    body = await recipe_service.body_of(db, revision.recipe_hash)
    seen: list[set[str] | None] = []
    validation, _ = recipe_service.validate_body(
        body, set(columns), seen, rule_sets=await recipe_service.preload_rule_sets(db, body)
    )
    validation.columns_checked = "inputs"
    if designer_check is not None:
        validation = await designer_check(validation, body, seen)
    out: dict[str, Any] = {
        "dataset_id": dataset.id,
        "recipe_revision_id": revision.id,
        "valid": validation.valid,
        "validation": validation.model_dump(mode="json"),
        "rows_in": sum(i.rows for i in inputs),
        "column_roles": None,
    }
    try:
        out["column_roles"] = initial_roles(
            dataset.target_type,
            [c["name"] for i in inputs for c in i.columns],
            detected_text=[t for i in inputs for t in i.detected_text],
            parent_roles=next((i.roles for i in inputs if i.roles is not None), None),
            overrides=dict(req.column_roles),
        )
    except Exception as exc:  # no_content_columns, column_not_found: the build would refuse
        out["valid"] = False
        out["column_roles_error"] = {
            "code": getattr(exc, "code", "column_roles_invalid"),
            "message": getattr(exc, "message", str(exc)),
        }
    settings = get_settings()
    reg = registry()
    plan = labelling_plan(body, inputs) if validation.valid else None
    steps = plan.steps if plan is not None else []
    window = await agent_label_ledger.window_total(db, plan.scope) if plan is not None else 0
    counted = plan.rows_total if plan is not None else 0
    threshold = settings.agent_label_row_threshold
    out["labelling"] = {
        "counted_steps": [
            {"step": index, "operator": info.ref, "why": _why(info)} for index, info in steps
        ],
        "rows_per_step": plan.rows_per_step if plan is not None else 0,
        "rows_counted": counted,
        "scope": plan.scope if plan is not None else None,
        "window_hours": settings.agent_label_window_hours,
        "window_total_now": window,
        "threshold": threshold,
        "agent_needs_approval": bool(steps) and window + counted > threshold,
        "approval_action": "agent_label_rows",
        "rule": "A step counts when it calls a model through an endpoint role, or is a probe-"
        "verdict or feature-tagging labeler; each counts the build's input rows (an upper bound). "
        "Data Designer expressions, filters and balancers do not count. The operator never waits.",
    }
    balancers = []
    if validation.valid:
        paths = await _input_files(db, list(req.inputs))
        for index, step in enumerate(body.get("steps", []), start=1):
            info = reg.get(step["operator"], step["version"])
            if info.name == BALANCER:
                seed = req.seed if req.seed is not None else 0
                balancers.append(
                    _balancer(index, dict(step.get("params") or {}), columns, paths, seed)
                )
    out["balancers"] = balancers
    out["writes"] = "nothing: no job, no ledger row, no version"
    return out
