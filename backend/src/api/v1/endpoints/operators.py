"""Operator routes (FR-003.24, FR-003.7, FR-003.11, FR-003.12, FR-003.19, FR-003.20; FTDD 003 5.1).

Every route reads the LIVE registry (``operators.registry.current()``). The allowlist write routes
refuse agent origin with ``403 agent_forbidden`` before anything is written (P-09) and record the
Settings operator name as who changed it (C5); feature 010 lists them as UI-only exemptions.
No route here is approval-gated (FTDD 003 section 5.3).

A preview waits up to ``OPERATOR_PREVIEW_SYNC_WAIT_S`` for its answer and otherwise answers
``202 {preview_id}``; the client polls ``GET /operators/previews/{id}``. Previews are not jobs:
there is no ``dw_jobs`` row and no socket room.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import Actor, refuse_agent_origin, resolve_who
from ....core.config import get_settings
from ....core.database import get_db, get_sync_db
from ....operators import allowlist, preview, schema_subset
from ....operators.errors import OperatorError
from ....operators.registry import OperatorRegistry, RegistryEntry, current
from ....schemas.operators import AllowlistIn, ParamsIn, PreviewIn, UpgradePlanIn
from ._paging import Page, paging

router = APIRouter(prefix="/api/v1/operators", tags=["operators"])

PREVIEW_TASK = "midataworks.operators.preview"
DJ_PREVIEW_TASK = "midataworks.datajuicer.preview"
DESIGNER_PREVIEW_TASK = "midataworks.designer.preview"


def registry() -> OperatorRegistry:
    return current()


def entry_out(entry: RegistryEntry, state: str, *, full: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": entry.name,
        "version": entry.version,
        "ref": entry.ref,
        "state": state,
        "origin": entry.origin,
        "provider": entry.provider,
        "error": entry.error,
        "manifest_hash": entry.manifest_hash,
        "entry_point": list(entry.entry_point) if entry.entry_point else None,
    }
    manifest = entry.manifest
    if manifest is not None:
        out.update(
            kind=manifest.kind,
            description=manifest.description,
            provider_version=manifest.provider_version,
            scope=manifest.scope,
            has_thresholds=bool(manifest.thresholds),
            queue=manifest.resources.queue,
            endpoint_role=manifest.resources.endpoint_role,
        )
        if full:
            out["manifest"] = manifest.model_dump(mode="json")
    else:
        out.update(kind=None, description=None, provider_version=None, scope=None)
        out["has_thresholds"] = False
    return out


@router.get("")
async def list_operators(
    provider: str | None = Query(None, max_length=128),
    kind: str | None = Query(None, max_length=32),
    state: str | None = Query(None, max_length=32),
    page: Page = Depends(paging),
) -> dict[str, Any]:
    rows = await run_in_threadpool(registry().entries)
    items = [entry_out(e, s) for e, s in rows]
    if provider:
        items = [i for i in items if i["provider"] == provider or i["origin"] == provider]
    if kind:
        items = [i for i in items if i["kind"] == kind]
    if state:
        items = [i for i in items if i["state"] == state]
    total = len(items)
    return {
        "items": items[page.offset : page.offset + page.limit],
        "total": total,
        "page": page.page,
        "limit": page.limit,
        "summary": await run_in_threadpool(registry().summary),
    }


@router.get("/schema-subset")
async def get_schema_subset() -> dict[str, Any]:
    return schema_subset.SUBSET


@router.get("/allowlist")
async def get_allowlist() -> dict[str, Any]:
    reg = registry()

    def read() -> dict[str, Any]:
        with get_sync_db() as session:
            allowed = allowlist.allowed_triples(session)
            history = allowlist.history(session)
        items = []
        for info in reg.entry_points():
            items.append(
                {
                    "distribution": info.distribution,
                    "distribution_version": info.distribution_version,
                    "entry_point": info.name,
                    "value": info.value,
                    "state": "allowed" if info.triple in allowed else "not_allowed",
                    "operators": [e.ref for e in reg.operators_of(info.triple)],
                    "history": [
                        {
                            "action": h.action,
                            "reason": h.reason,
                            "changed_by": h.changed_by,
                            "created_at": h.created_at.isoformat(),
                        }
                        for h in history
                        if (h.distribution, h.distribution_version, h.entry_point) == info.triple
                    ],
                }
            )
        return {"items": items}

    return await run_in_threadpool(read)


async def _write_allowlist(
    body: AllowlistIn, action: str, actor: Actor, db: AsyncSession
) -> dict[str, Any]:
    who = await resolve_who(actor, db)
    triple = (body.distribution, body.distribution_version, body.entry_point)
    reg = registry()
    if triple not in {e.triple for e in reg.entry_points()}:
        raise OperatorError(
            "entry_point_not_installed",
            f"No entry point {body.entry_point!r} from {body.distribution} "
            f"{body.distribution_version} is installed. Packages reach the image only by a commit "
            "to its pinned requirements (see How to add an operator package).",
            {"distribution": body.distribution, "entry_point": body.entry_point},
        )

    def write() -> None:
        with get_sync_db() as session:
            if action == "allow":
                allowlist.allow(session, triple, body.reason, who.who)
            else:
                allowlist.revoke(session, triple, body.reason, who.who)

    await run_in_threadpool(write)
    state = "allowed" if reg.is_entry_point_allowed(triple) else "not_allowed"
    return {
        "distribution": body.distribution,
        "distribution_version": body.distribution_version,
        "entry_point": body.entry_point,
        "action": action,
        "state": state,
        "changed_by": who.who,
        "operators": [e.ref for e in reg.operators_of(triple)],
    }


@router.post("/allowlist", status_code=201)
async def allow_entry_point(
    body: AllowlistIn,
    actor: Actor = Depends(refuse_agent_origin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await _write_allowlist(body, "allow", actor, db)


@router.post("/allowlist/revoke", status_code=201)
async def revoke_entry_point(
    body: AllowlistIn,
    actor: Actor = Depends(refuse_agent_origin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await _write_allowlist(body, "revoke", actor, db)


@router.post("/upgrade-plan")
async def upgrade_plan(body: UpgradePlanIn) -> dict[str, Any]:
    """Per step: the current version and whether the step's params are valid against it (T-11).
    Never fills a removed parameter in."""
    reg = registry()

    def plan() -> list[dict[str, Any]]:
        steps = []
        for index, step in enumerate(body.recipe_body.steps, start=1):
            to = reg.current_version(step.operator)
            errors: list[dict[str, str]] = []
            if to is None:
                errors = [{"pointer": "", "message": f"No runnable version of {step.operator}."}]
            else:
                errors = reg.validate_params_detailed(step.operator, to, step.params)
            steps.append(
                {
                    "index": index,
                    "operator": step.operator,
                    "from": step.version,
                    "to": to,
                    "changes": to is not None and to != step.version,
                    "params_valid": to is not None and not errors,
                    "errors": errors,
                }
            )
        return steps

    return {"steps": await run_in_threadpool(plan)}


@router.get("/previews/{preview_id}")
async def get_preview(preview_id: str) -> dict[str, Any]:
    body = await run_in_threadpool(preview.fetch, preview_id)
    if body is None:
        raise OperatorError(
            "preview_not_found",
            "No preview with that id: it finished more than an hour ago or never existed. "
            "Run the preview again.",
            {"preview_id": preview_id},
        )
    return body


@router.get("/{name}")
async def get_operator_versions(name: str) -> dict[str, Any]:
    rows = [(e, s) for e, s in await run_in_threadpool(registry().entries) if e.name == name]
    if not rows:
        raise OperatorError(
            "operator_not_found", f"No operator named {name} is installed.", {"operator": name}
        )
    return {
        "name": name,
        "current_version": await run_in_threadpool(registry().current_version, name),
        "versions": [entry_out(e, s) for e, s in rows],
    }


def _state_of(reg: OperatorRegistry, entry: RegistryEntry) -> str:
    for candidate, state in reg.entries():
        if candidate.ref == entry.ref:
            return state
    return entry.state


@router.get("/{name}/{version}")
async def get_operator(name: str, version: str) -> dict[str, Any]:
    reg = registry()
    entry = await run_in_threadpool(reg.entry, name, version)
    state = await run_in_threadpool(_state_of, reg, entry)
    return entry_out(entry, state, full=True)


@router.post("/{name}/{version}/validate")
async def validate_params(name: str, version: str, body: ParamsIn) -> dict[str, Any]:
    """Validate params for a run: the operator must be allowed (10.5), the params valid (FR-003.7)."""
    await run_in_threadpool(registry().require_allowed, name, version)
    await run_in_threadpool(registry().require_valid_params, name, version, body.params)
    return {"valid": True}


async def _resolve_input(db: AsyncSession, body: PreviewIn) -> dict[str, Any]:
    from ....core.storage import resolve_under_data_dir
    from ....models.step_execution import StepExecution
    from ....models.version import Version, VersionBuild
    from ....services.step_contract import part_files

    source = body.input
    if source.version_id is not None:
        version = await db.get(Version, source.version_id)
        if version is None:
            raise OperatorError(
                "input_not_found", "No such version.", {"version_id": source.version_id}
            )
        splits = [s for s in version.splits if source.split is None or s["name"] == source.split]
        if not splits:
            raise OperatorError(
                "input_not_found",
                f"Version {version.number} has no split {source.split!r}.",
                {"split": source.split},
            )
        return {
            "files": [s["path"] for s in splits],
            "column_roles": dict(version.column_roles),
            "rowkey_scheme": version.rowkey_scheme,
        }
    step = await db.get(StepExecution, source.step_execution_id)
    if step is None or step.state != "completed":
        raise OperatorError(
            "input_not_found",
            "No completed step execution with that id.",
            {"step_execution_id": source.step_execution_id},
        )
    build = await db.get(VersionBuild, step.job_id)
    if build is None:
        raise OperatorError("input_not_found", "The step's build record is gone.")
    directory = resolve_under_data_dir(step.output_dir)
    root = resolve_under_data_dir()
    return {
        "files": [str(p.relative_to(root)) for p in part_files(directory)],
        "column_roles": dict(step.output_column_roles or {}),
        "rowkey_scheme": str(build.request["rowkey_scheme"]),
    }


def send_preview(request: dict[str, Any], entry: RegistryEntry) -> None:
    """Queue the preview (tests replace this to run it in-process)."""
    from ....core.celery_app import celery_app, linked_signature

    if entry.manifest is not None and entry.manifest.resources.queue == "datajuicer":
        payload = {
            "op_name": entry.extra["op_name"],
            "kind": entry.manifest.kind,
            "stats_key": entry.extra.get("stats_key"),
            "params": request["params"],
            "params_schema": entry.manifest.params_schema,
            "files": request["resolved_input"]["files"],
            "sample_size": request["sample_size_effective"],
            "seed": request["seed"],
            "num_proc": get_settings().dj_num_proc,
        }
        shape = linked_signature(PREVIEW_TASK, args=[{**request, "datajuicer": True}])
        celery_app.send_task(DJ_PREVIEW_TASK, args=[payload], link=shape)
        return
    if entry.manifest is not None and entry.manifest.resources.queue == "designer":
        celery_app.send_task(
            DESIGNER_PREVIEW_TASK,
            args=[designer_preview_payload(request, entry)],
            link=linked_signature(PREVIEW_TASK, args=[{**request, "designer": True}]),
        )
        return
    celery_app.send_task(PREVIEW_TASK, args=[request])


def designer_preview_payload(request: dict[str, Any], entry: RegistryEntry) -> dict[str, Any]:
    """The designer worker's preview payload; the model key is sealed, never in the message."""
    import uuid

    from ....operators import endpoint_port
    from ....operators.data_designer import handoff
    from ....operators.data_designer.adapter import designer_payload

    assert entry.manifest is not None
    endpoint = None
    key_ref = None
    role = entry.manifest.resources.endpoint_role
    if role is not None:
        endpoint = endpoint_port.resolver().resolve(role)
        if endpoint.api_key:
            key_ref = f"preview:{request['preview_id']}:{uuid.uuid4().hex}"
            handoff.put(key_ref, endpoint.api_key)
    resolved = request["resolved_input"]
    return {
        **designer_payload(entry, request["params"], resolved["column_roles"], endpoint, key_ref),
        "files": resolved["files"],
        "sample_size": request["sample_size_effective"],
        "seed": request["seed"],
    }


async def _preview(name: str, version: str, body: PreviewIn, mode: str, db: AsyncSession) -> Any:
    reg = registry()
    entry = await run_in_threadpool(reg.require_allowed, name, version)
    await run_in_threadpool(reg.require_valid_params, name, version, body.params)
    if mode == "statistics" and (entry.manifest is None or not entry.manifest.thresholds):
        raise OperatorError(
            "no_threshold",
            f"{entry.ref} declares no threshold, so it has no statistic to plot.",
            {"operator": entry.ref},
        )
    size = preview.sample_size_for(entry.manifest, body.sample_size)
    request: dict[str, Any] = {
        "operator": name,
        "version": version,
        "params": body.params,
        "input": body.input.model_dump(),
        "sample_size": body.sample_size,
        "seed": body.seed,
        "mode": mode,
    }
    preview_id = preview.request_hash(request)
    request["preview_id"] = preview_id
    request["sample_size_effective"] = size
    request["resolved_input"] = await _resolve_input(db, body)
    existing = await run_in_threadpool(preview.fetch, preview_id)
    if existing is None:
        claimed = await run_in_threadpool(preview.claim, preview_id)
        if claimed:
            await run_in_threadpool(send_preview, request, entry)
    deadline = asyncio.get_running_loop().time() + get_settings().operator_preview_sync_wait_s
    while True:
        answer = await run_in_threadpool(preview.fetch, preview_id)
        if answer is not None and answer.get("status") in {"done", "failed"}:
            break
        if asyncio.get_running_loop().time() >= deadline:
            return JSONResponse(
                status_code=202, content={"preview_id": preview_id, "status": "running"}
            )
        await asyncio.sleep(0.1)
    if answer["status"] == "failed":
        error = answer["error"]
        raise OperatorError(error["code"], error["message"], error.get("details"))
    return answer


@router.post("/{name}/{version}/preview")
async def preview_operator(
    name: str, version: str, body: PreviewIn, db: AsyncSession = Depends(get_db)
) -> Any:
    return await _preview(name, version, body, "preview", db)


@router.post("/{name}/{version}/statistics")
async def operator_statistics(
    name: str, version: str, body: PreviewIn, db: AsyncSession = Depends(get_db)
) -> Any:
    return await _preview(name, version, body, "statistics", db)
