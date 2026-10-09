"""Build requests: validate, resolve, dedupe, create the job (FR-002.2, 002.5, 002.7, 002.16, 002.50).

FTID 002 section 3.4. A request is resolved completely BEFORE a job exists — inputs, revision,
recipe validity (against the inputs' real columns), bindings, roles, seed — so every refusal reaches
the caller as a reason, not as a failed job. Then, under an advisory lock on the request digest:
an existing completed version is returned (``200``, nothing built, FR-002.5); an identical build
already running is returned (``202``, ``existing_job: true``); otherwise one job is created.

The seed is defaulted here, at request time, and recorded; the request digest is computed after
defaulting, so it is stable for the build it names (FTID IQ10).
"""

from __future__ import annotations

import logging
import secrets
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.agent_origin import Actor, Who
from ..core.errors import ConflictError, NotFoundError
from ..core.ids import new_id
from ..models.dataset import Dataset
from ..models.enums import InputKind, VersionState
from ..models.job import Job
from ..models.recipe import Recipe, RecipeRevision
from ..models.version import Version, VersionBuild
from ..schemas.versions import VersionBuildRequest
from . import agent_label_ledger, recipe_service
from .assembly import initial_roles
from .bindings import resolve_bindings
from .identity import VERSION_SEED_LIMIT, request_digest
from .operator_port import OperatorInfo, registry
from .row_keys import ROWKEY_V1
from .sources.reader import get_ready_source, list_files

logger = logging.getLogger(__name__)

BUILD_KIND = "version_build"
LIVE_JOB_STATES = ("queued", "running", "cancelling")


@dataclass(frozen=True)
class ResolvedInput:
    """One input, pinned: what the request digest, the manifest and assembly all read."""

    canonical: dict[str, Any]
    columns: list[dict[str, Any]]
    rows: int
    #: A version input's roles; None for a source.
    roles: dict[str, str] | None = None
    detected_text: tuple[str, ...] = ()


def _uuid(value: str, code: str, what: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise NotFoundError(f"{value!r} is not a valid {what} id.", code=code) from None


async def get_dataset(db: AsyncSession, dataset_id: str) -> Dataset:
    row = await db.get(Dataset, _uuid(dataset_id, "dataset_not_found", "dataset"))
    if row is None:
        raise NotFoundError(f"No dataset {dataset_id}.", code="dataset_not_found")
    return row


async def resolve_inputs(db: AsyncSession, inputs: list[Any]) -> list[ResolvedInput]:
    """Pin every input; refuse a source that is not ready or not pinned, or a deleted version."""
    resolved: list[ResolvedInput] = []
    for ref in inputs:
        if ref.kind == InputKind.SOURCE:
            source = await get_ready_source(db, ref.source_id)  # source_not_ready, source_unpinned
            files = await list_files(db, ref.source_id)
            columns: dict[str, dict[str, Any]] = {}
            for f in files:
                for column in f.columns:
                    columns.setdefault(column["name"], column)
            detected = tuple((source.detection or {}).get("text_columns") or ())
            resolved.append(
                ResolvedInput(
                    canonical={"kind": "source", "source_id": source.id, **source.pin},
                    columns=list(columns.values()),
                    rows=sum(f.rows for f in files),
                    detected_text=detected,
                )
            )
        else:
            vid = _uuid(ref.version_id, "version_not_found", "version")
            version = await db.get(Version, vid)
            if version is None:
                raise NotFoundError(f"No version {ref.version_id}.", code="version_not_found")
            if version.state == VersionState.DELETED:
                raise ConflictError(
                    "That version was deleted; its rows are gone. Build from a version that still "
                    "has rows, or from its sources.",
                    code="version_deleted",
                    details={"version_id": version.id},
                )
            user_roles = {c: r for c, r in version.column_roles.items() if r != "system"}
            resolved.append(
                ResolvedInput(
                    canonical={
                        "kind": "version",
                        "version_id": version.id,
                        "manifest_sha256": version.manifest_sha256,
                    },
                    columns=[{"name": c} for c in user_roles],
                    rows=int(version.total_rows),
                    roles=user_roles,
                )
            )
    return resolved


async def get_buildable_revision(
    db: AsyncSession, revision_id: str
) -> tuple[Recipe, RecipeRevision]:
    if not revision_id:
        raise NotFoundError("The recipe has no revision to build.", code="revision_not_found")
    revision = await recipe_service.get_revision_row(db, revision_id)
    recipe = await recipe_service.get_recipe_row(db, revision.recipe_id)
    if recipe.archived:
        raise ConflictError(
            f"Recipe {recipe.name!r} is archived and cannot start new builds. Clone it to build.",
            code="recipe_archived",
            details={"recipe_id": recipe.id},
        )
    return recipe, revision


@dataclass
class LabellingPlan:
    """The steps that label rows, for the P-07 gate (FR-002.50)."""

    scope: str
    steps: list[tuple[int, OperatorInfo]] = field(default_factory=list)
    rows_per_step: int = 0

    @property
    def rows_total(self) -> int:
        return self.rows_per_step * len(self.steps)


def build_scope(inputs: list[ResolvedInput]) -> str:
    """The ledger scope: the first input version, else the first source (agent_label_row.py)."""
    for item in inputs:
        if item.canonical["kind"] == "version":
            return str(item.canonical["version_id"])
    return str(inputs[0].canonical["source_id"])


def labelling_plan(body: dict[str, Any], inputs: list[ResolvedInput]) -> LabellingPlan:
    plan = LabellingPlan(scope=build_scope(inputs), rows_per_step=sum(i.rows for i in inputs))
    reg = registry()
    for index, step in enumerate(body.get("steps", []), start=1):
        info = reg.get(step["operator"], step["version"])  # an unknown operator raises: gated
        if info.labels_rows:
            plan.steps.append((index, info))
    return plan


async def labelling_plan_for_route(db: AsyncSession, values: dict[str, Any]) -> LabellingPlan:
    """Resolve the gated route's stored values into a plan (``POST /versions`` or ``/recipes/
    {id}/build``)."""
    request = values["body"]
    if "recipe_id" in values:
        recipe = await recipe_service.get_recipe_row(db, values["recipe_id"])
        request = request.to_build_request(recipe.head_revision_id)
    _, revision = await get_buildable_revision(db, request.recipe_revision_id)
    body = await recipe_service.body_of(db, revision.recipe_hash)
    inputs = await resolve_inputs(db, request.inputs)
    return labelling_plan(body, inputs)


# --------------------------------------------------------------------------------------------
# The request
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BuildOutcome:
    kind: str  # "existing" | "running" | "started"
    version_id: str | None = None
    job_id: str | None = None
    seed: int = 0
    request_digest: str = ""


def _digest_lock(digest: str) -> int:
    return int(digest[:15], 16)


async def request_build(
    db: AsyncSession, req: VersionBuildRequest, who: Who, actor: Actor
) -> BuildOutcome:
    dataset = await get_dataset(db, req.dataset_id)
    _, revision = await get_buildable_revision(db, req.recipe_revision_id)
    inputs = await resolve_inputs(db, req.inputs)
    available = {c["name"] for i in inputs for c in i.columns}
    body = await recipe_service.body_of(db, revision.recipe_hash)
    recipe_service.require_valid(body, available)  # recipe_invalid, every step listed
    bindings = await resolve_bindings(db, [b.model_dump() for b in req.bindings])
    roles = initial_roles(
        dataset.target_type,
        [c["name"] for i in inputs for c in i.columns],
        detected_text=[t for i in inputs for t in i.detected_text],
        parent_roles=next((i.roles for i in inputs if i.roles is not None), None),
        overrides=dict(req.column_roles),
    )  # no_content_columns, column_not_found
    seed = req.seed if req.seed is not None else secrets.randbelow(VERSION_SEED_LIMIT)
    canonical_inputs = [i.canonical for i in inputs]
    digest = request_digest(
        dataset_id=dataset.id,
        inputs=canonical_inputs,
        recipe_hash_hex=revision.recipe_hash,
        seed=seed,
        bindings=bindings,
        rowkey_scheme=ROWKEY_V1,
        column_role_overrides=dict(req.column_roles),
    )
    await db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _digest_lock(digest)})
    existing = (
        await db.execute(
            select(Version.id).where(
                Version.request_digest == digest, Version.state == VersionState.COMPLETED
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return BuildOutcome("existing", version_id=str(existing), seed=seed, request_digest=digest)
    running = (
        await db.execute(
            select(VersionBuild.job_id)
            .join(Job, Job.id == VersionBuild.job_id)
            .where(
                VersionBuild.request_digest == digest,
                VersionBuild.verify_version_id.is_(None),
                Job.status.in_(LIVE_JOB_STATES),
            )
        )
    ).scalar_one_or_none()
    if running is not None:
        return BuildOutcome("running", job_id=running, seed=seed, request_digest=digest)

    job = Job(
        id=new_id("job"),
        kind=BUILD_KIND,
        status="queued",
        progress=0.0,
        params={
            "dataset_id": dataset.id,
            "recipe_revision_id": revision.id,
            "seed": seed,
            "request_digest": digest,
        },
        started_by=who.who,
        started_by_origin=who.origin,
    )
    db.add(job)
    await db.flush()
    db.add(
        VersionBuild(
            job_id=job.id,
            dataset_id=dataset.id,
            request_digest=digest,
            request={
                "dataset_id": dataset.id,
                "inputs": canonical_inputs,
                "recipe_revision_id": revision.id,
                "recipe_hash": revision.recipe_hash,
                "seed": seed,
                "bindings": bindings,
                "rowkey_scheme": ROWKEY_V1,
                "column_role_overrides": dict(req.column_roles),
                "initial_roles": roles,
            },
            reuse_enabled=True,
            sources_verified=False,
            steps=[],
            current_step_index=0,
        )
    )
    if actor.origin == "agent":
        plan = labelling_plan(body, inputs)
        for index, info in plan.steps:
            await agent_label_ledger.admit(
                db, plan.scope, who.who, info.run_kind, f"{job.id}:{index}", plan.rows_per_step
            )
    await db.commit()
    return BuildOutcome("started", job_id=job.id, seed=seed, request_digest=digest)
