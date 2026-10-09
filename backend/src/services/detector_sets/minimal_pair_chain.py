"""The minimal-pair chain: 009's minimal-pair generator as a chain of existing pieces.

Operator decision 2026-10-07 (FTASKS 15.0). The single ``minimal_pair_generator@1`` operator could
not work: it needed TWO endpoint roles while 003's run context resolves one, and its judge check is
a 005 label run, which an operator (no database session) cannot write. So:

``generate``  a 007 generation run in ``minimal_pairs`` mode — seeds, held-out rules, steering,
              records, judge independence at plan and in the worker (FR-009.62 - FR-009.64);
``scope``     a 002 build: ``dw_generated_rows@1`` then ``minimal_pair_scope@1`` — the counterparts
              within the edit cap and their seeds, nothing else;
``judge``     a 005 judge label run with the PINNED rubric over that version — 005's preflight
              refuses it again if the judge is the generator (``generation/independence.py``);
``pair``      a 002 build: ``minimal_pair_join@1`` — verified flips only, ``pair_id``, provenance.

**Refusals come first** (:func:`plan`, which writes nothing): the rubric and the flip verdicts, then
007's own plan — held out missing, held-out seed splits, a judge that is the generator — and a judge
that is not configured at all (the chain cannot verify anything without one).

**The chain advances itself.** Beat calls :func:`advance` for every running chain
(``workers/minimal_pair_tasks.py``); a stage that completed starts the next; a stage that FAILED,
was cancelled, or refused to start stops the chain as ``failed`` with ``failed_stage`` naming it and
``error`` saying why. :func:`resume` continues the stopped stage (its own resume for a run, a new
build for a build, a new start for a refused start) and the chain carries on.

Only this module writes ``dw_minimal_pair_chains``.
"""

from __future__ import annotations

import logging
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.agent_origin import Actor, Who
from ...core.clock import utc_now
from ...core.errors import AppError, ConflictError, NotFoundError, UnprocessableError
from ...core.ids import new_id
from ...models.generation import GenerationRun
from ...models.job import TERMINAL_STATUSES, Job
from ...models.label_run import LabelRun
from ...models.minimal_pair_chain import RESUMABLE_CHAIN_STATES, MinimalPairChain
from ...models.recipe import Recipe
from ...models.rubric import Rubric
from ...models.step_execution import StepExecution
from ...models.version import VersionBuild, VersionStep
from ...schemas.generation import GenerationRunCreate
from ...schemas.labeling import LabelRunStart, RubricBody, Sampling
from ...schemas.minimal_pairs import (
    ChainStage,
    JudgePlan,
    MinimalPairChainCreate,
    MinimalPairChainOut,
    MinimalPairChainPlan,
)
from ...schemas.versions import BindingRef, VersionBuildRequest, VersionInputRef
from ..generation import run_service as generation
from ..version_read_service import get_version_row

logger = logging.getLogger(__name__)

STAGES: tuple[str, ...] = ("generate", "scope", "judge", "pair")
SCOPE_RECIPE_PREFIX = "minimal-pairs-scope-"
JOIN_RECIPE_PREFIX = "minimal-pairs-join-"
#: Reasons the join drops BOTH rows of a pair with (the rest are per row).
PAIR_REASONS: tuple[str, ...] = (
    "flip_not_verified",
    "seed_not_flip_from",
    "judge_missing",
    "judge_provisional",
)


class StageRefused(Exception):
    """A stage could not start; the chain stops at it with this code and message."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


# --- the plan: every refusal before anything runs ------------------------------------------


@dataclass
class Planned:
    out: MinimalPairChainPlan
    generation_request: GenerationRunCreate
    generation: generation.Planned


def generation_request(req: MinimalPairChainCreate) -> GenerationRunCreate:
    """The 007 run the chain's first stage is (mode ``minimal_pairs``, one edit per seed)."""
    return GenerationRunCreate(
        mode="minimal_pairs",
        input_version_id=req.input_version_id,
        prompt_column=req.text_column,
        seed_splits=list(req.seed_splits),
        sample_size=req.sample_size,
        seed=req.seed,
        n_responses=1,
        respond_template_id=req.respond_template_id,
        generator_setting=req.generator_setting,
        target_type="detector",
    )


async def _rubric(db: AsyncSession, req: MinimalPairChainCreate) -> tuple[Rubric, RubricBody, str]:
    rubric = await db.get(Rubric, req.rubric_id)
    if rubric is None:
        raise NotFoundError(f"No rubric {req.rubric_id}.", code="RUBRIC_NOT_FOUND")
    body = RubricBody.model_validate(rubric.body)
    if body.style == "pairwise":
        raise UnprocessableError(
            f"Rubric {rubric.name}@{rubric.version} is pairwise; a minimal pair's judge reads ONE "
            "row at a time and gives it a verdict.",
            code="RUBRIC_STYLE_UNSUPPORTED",
            details={"style": body.style},
        )
    missing = [v for v in (req.flip_from, req.flip_to) if v not in body.allowed_verdicts]
    if missing:
        raise UnprocessableError(
            f"The rubric's verdicts are {body.allowed_verdicts}; {missing} is not one of them.",
            code="FLIP_VERDICT_UNKNOWN",
            details={"allowed_verdicts": list(body.allowed_verdicts), "unknown": missing},
        )
    field = req.judge_field
    if field is None:
        if len(body.input_fields) != 1:
            raise UnprocessableError(
                f"The rubric reads {body.input_fields}; name the one the text goes into.",
                code="JUDGE_FIELD_REQUIRED",
                details={"input_fields": list(body.input_fields)},
            )
        field = body.input_fields[0]
    elif field not in body.input_fields:
        raise UnprocessableError(
            f"The rubric has no input field {field!r}; it reads {body.input_fields}.",
            code="JUDGE_FIELD_UNKNOWN",
            details={"input_fields": list(body.input_fields)},
        )
    return rubric, body, field


async def plan(db: AsyncSession, req: MinimalPairChainCreate, origin: str) -> Planned:
    """Every refusal, in order, before a row, a file or a job exists (FTASKS 15.2)."""
    rubric, body, field = await _rubric(db, req)
    gen_req = generation_request(req)
    # 007's plan: held-out first (FR-009.64 / FR-007.35), held-out seed splits (FR-007.36), the
    # template, the steering, and judge independence at its API call site (FR-009.62 / T-35).
    planned = await generation.plan(db, gen_req, origin)
    judge = planned.judge_identity
    if judge is None:
        raise ConflictError(
            "No judge is configured. A minimal pair is kept only when a judge verifies the flip; "
            "set Settings → Endpoints → judge to a model other than the generator.",
            code="JUDGE_UNCONFIGURED",
            details={"role": "judge"},
        )
    out = MinimalPairChainPlan(
        stages=list(STAGES),
        generation=planned.out,
        judge=JudgePlan(
            model_id=judge.model_id,
            identity=judge.as_dict(),
            rubric_id=rubric.id,
            rubric_ref=f"{rubric.name}@{rubric.version}",
            allowed_verdicts=list(body.allowed_verdicts),
            field_map={field: req.text_column},
        ),
        flip_from=req.flip_from,
        flip_to=req.flip_to,
        max_edit_chars=req.max_edit_chars,
        max_edit_words=req.max_edit_words,
        judge_rows_at_most=2 * planned.out.seed_rows_selected,
    )
    return Planned(out=out, generation_request=gen_req, generation=planned)


async def start(
    db: AsyncSession, req: MinimalPairChainCreate, planned: Planned, who: Who
) -> MinimalPairChain:
    """Start stage one (the generation run) and record the chain beside it."""
    run = await generation.start(db, planned.generation_request, planned.generation, who)
    request = req.model_dump(mode="json")
    request["judge_field"] = next(iter(planned.out.judge.field_map))
    chain = MinimalPairChain(
        id=new_id("mpc"),
        state="running",
        stage="generate",
        input_version_id=req.input_version_id,
        request=request,
        generation_run_id=run.id,
        counts={},
        started_by=who.who,
        started_by_origin=who.origin,
        acting_by=who.who,
        acting_origin=who.origin,
    )
    db.add(chain)
    await db.commit()
    logger.info("minimal_pair_chain.started chain=%s run=%s", chain.id, run.id)
    return chain


# --- reads ---------------------------------------------------------------------------------


async def get_chain(db: AsyncSession, chain_id: str) -> MinimalPairChain:
    chain = await db.get(MinimalPairChain, chain_id, populate_existing=True)
    if chain is None:
        raise NotFoundError(f"No minimal-pair chain {chain_id}.", code="CHAIN_NOT_FOUND")
    return chain


async def _job(db: AsyncSession, job_id: str | None) -> Job | None:
    if job_id is None:
        return None
    return await db.get(Job, job_id, populate_existing=True)


async def stages_of(db: AsyncSession, chain: MinimalPairChain) -> list[ChainStage]:
    run = await db.get(GenerationRun, chain.generation_run_id, populate_existing=True)
    judge = (
        await db.get(LabelRun, chain.judge_run_id, populate_existing=True)
        if chain.judge_run_id
        else None
    )

    def build_state(job: Job | None, version_id: str | None) -> str:
        if version_id is not None:
            return "completed"
        return job.status if job is not None else "pending"

    scope_job = await _job(db, chain.scope_job_id)
    pair_job = await _job(db, chain.pair_job_id)
    return [
        ChainStage(
            stage="generate",
            kind="generation_run",
            run_id=chain.generation_run_id,
            job_id=None,
            version_id=None,
            state=run.state if run is not None else "missing",
        ),
        ChainStage(
            stage="scope",
            kind="version_build",
            run_id=None,
            job_id=chain.scope_job_id,
            version_id=chain.scope_version_id,
            state=build_state(scope_job, chain.scope_version_id),
        ),
        ChainStage(
            stage="judge",
            kind="label_run",
            run_id=chain.judge_run_id,
            job_id=None,
            version_id=None,
            state=judge.state if judge is not None else "pending",
        ),
        ChainStage(
            stage="pair",
            kind="version_build",
            run_id=None,
            job_id=chain.pair_job_id,
            version_id=chain.pair_version_id,
            state=build_state(pair_job, chain.pair_version_id),
        ),
    ]


async def chain_resumable(db: AsyncSession, chain: MinimalPairChain) -> bool:
    if chain.state not in RESUMABLE_CHAIN_STATES:
        return False
    if chain.stage == "generate":
        run = await db.get(GenerationRun, chain.generation_run_id)
        return run is not None and (run.state == "completed" or generation.resumable(run))
    return True


async def chain_out(db: AsyncSession, chain: MinimalPairChain) -> MinimalPairChainOut:
    return MinimalPairChainOut(
        id=chain.id,
        state=chain.state,
        stage=chain.stage,
        input_version_id=str(chain.input_version_id),
        request=dict(chain.request),
        stages=await stages_of(db, chain),
        failed_stage=chain.failed_stage,
        error=chain.error,
        counts=dict(chain.counts or {}),
        pair_version_id=chain.pair_version_id,
        resumable=await chain_resumable(db, chain),
        started_by=chain.started_by,
        started_by_origin=chain.started_by_origin,
        acting_by=chain.acting_by,
        acting_origin=chain.acting_origin,
        created_at=chain.created_at,
        updated_at=chain.updated_at,
        completed_at=chain.completed_at,
    )


async def list_chains(
    db: AsyncSession, *, state: str | None, input_version_id: str | None, page: int, limit: int
) -> tuple[list[MinimalPairChain], int]:
    query = select(MinimalPairChain)
    if state:
        query = query.where(MinimalPairChain.state == state)
    if input_version_id:
        query = query.where(MinimalPairChain.input_version_id == input_version_id)
    total = int((await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one())
    rows = await db.execute(
        query.order_by(MinimalPairChain.created_at.desc(), MinimalPairChain.id)
        .offset((page - 1) * limit)
        .limit(limit)
    )
    return list(rows.scalars()), total


# --- advancing -----------------------------------------------------------------------------


@contextmanager
def chain_lock(chain_id: str) -> Iterator[bool]:
    """A session-level advisory lock on its own connection: two advances (Beat and a resume) never
    start the same stage twice. Yields whether this caller holds it."""
    from ...core.database import get_sync_engine

    key = zlib.crc32(f"minimal-pair-chain:{chain_id}".encode())
    with get_sync_engine().connect() as conn:
        held = bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar())
        try:
            yield held
        finally:
            if held:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
                conn.commit()


def _who(chain: MinimalPairChain) -> tuple[Who, Actor]:
    origin: Literal["operator", "agent"] = "agent" if chain.acting_origin == "agent" else "operator"
    who = Who(chain.acting_by, origin)
    actor = Actor(origin=origin, agent=chain.acting_by if origin == "agent" else None)
    return who, actor


async def _fail(
    db: AsyncSession,
    chain: MinimalPairChain,
    stage: str,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Stop the chain at ``stage``, naming it (the stage-failure stop)."""
    chain.state = "failed"
    chain.failed_stage = stage
    chain.error = {"code": code, "message": message, "stage": stage, "details": details or {}}
    await db.commit()
    logger.warning("minimal_pair_chain.failed chain=%s stage=%s code=%s", chain.id, stage, code)


def _dispatch() -> list[str]:
    from ...core.database import get_sync_db
    from ..job_service import dispatch_queued

    with get_sync_db() as session:
        return dispatch_queued(session)


async def _system_recipe(
    db: AsyncSession, who: Who, name: str, steps: list[dict[str, Any]], labels: list[str]
) -> Recipe:
    from .. import recipe_service

    found = (await db.execute(select(Recipe).where(Recipe.name == name))).scalar_one_or_none()
    if found is not None:
        return found
    return await recipe_service.create(
        db,
        who,
        name=name,
        description="System recipe: a stage of a minimal-pair chain (feature 009).",
        body={"format": "dw.recipe/v1", "steps": steps},
        step_labels=labels,
    )


async def _build(
    db: AsyncSession,
    chain: MinimalPairChain,
    *,
    recipe_name: str,
    steps: list[dict[str, Any]],
    labels: list[str],
    input_version_id: str,
    bindings: list[BindingRef],
) -> tuple[str | None, str | None]:
    """Request a 002 build; ``(job_id, None)`` when one runs, ``(None, version_id)`` when the
    identical build already exists."""
    from .. import version_build_service

    who, actor = _who(chain)
    recipe = await _system_recipe(db, who, recipe_name, steps, labels)
    assert recipe.head_revision_id is not None
    version = await get_version_row(db, input_version_id)
    run = await db.get(GenerationRun, chain.generation_run_id)
    assert run is not None
    request = VersionBuildRequest(
        dataset_id=str(version.dataset_id),
        inputs=[VersionInputRef(kind="version", version_id=str(version.id))],
        recipe_revision_id=str(recipe.head_revision_id),
        bindings=bindings,
        seed=int(run.seed),
    )
    outcome = await version_build_service.request_build(db, request, who, actor)
    if outcome.kind == "existing":
        return None, outcome.version_id
    if outcome.kind == "started":
        await run_in_threadpool(_dispatch)
    return outcome.job_id, None


async def _start_scope(db: AsyncSession, chain: MinimalPairChain) -> None:
    req = chain.request
    params: dict[str, Any] = {"generation_run_id": chain.generation_run_id}
    for name in ("max_edit_chars", "max_edit_words"):
        if req.get(name) is not None:
            params[name] = int(req[name])
    job_id, version_id = await _build(
        db,
        chain,
        recipe_name=f"{SCOPE_RECIPE_PREFIX}{chain.id}",
        steps=[
            {
                "operator": "dw_generated_rows",
                "version": "1",
                "params": {"generation_run_id": chain.generation_run_id, "target_type": "detector"},
            },
            {"operator": "minimal_pair_scope", "version": "1", "params": params},
        ],
        labels=["Add the counterparts", "Keep the pairs"],
        input_version_id=str(chain.input_version_id),
        bindings=[BindingRef(kind="generation_run", id=chain.generation_run_id)],
    )
    chain.scope_job_id, chain.scope_version_id = job_id, version_id
    await db.commit()


async def _start_judge(db: AsyncSession, chain: MinimalPairChain) -> None:
    from .. import label_run_service

    req = chain.request
    assert chain.scope_version_id is not None
    who, actor = _who(chain)
    start_req = LabelRunStart(
        input_version_id=str(chain.scope_version_id),
        role="judge",
        rubric_id=str(req["rubric_id"]),
        field_map={str(req["judge_field"]): str(req["text_column"])},
        sampling=(
            Sampling(seed=req.get("judge_seed")) if req.get("judge_seed") is not None else None
        ),
    )
    planned = await label_run_service.plan(db, start_req, who.origin)
    if planned.plan.approval_needed:
        raise StageRefused(
            "APPROVAL_NEEDED",
            f"The judge run would score {planned.plan.rows_to_score} rows, over the agent "
            f"threshold of {planned.plan.threshold} (P-07). An operator resumes the chain to "
            "start it.",
            {"rows_to_score": planned.plan.rows_to_score},
        )
    run = await label_run_service.start(db, start_req, planned, who, actor)
    chain.judge_run_id = run.id
    await db.commit()


async def _start_pair(db: AsyncSession, chain: MinimalPairChain) -> None:
    req = chain.request
    assert chain.scope_version_id is not None and chain.judge_run_id is not None
    job_id, version_id = await _build(
        db,
        chain,
        recipe_name=f"{JOIN_RECIPE_PREFIX}{chain.id}",
        steps=[
            {
                "operator": "minimal_pair_join",
                "version": "1",
                "params": {
                    "generation_run_id": chain.generation_run_id,
                    "judge_label_run_id": chain.judge_run_id,
                    "flip_from": str(req["flip_from"]),
                    "flip_to": str(req["flip_to"]),
                },
            }
        ],
        labels=["Keep verified flips"],
        input_version_id=str(chain.scope_version_id),
        bindings=[
            BindingRef(kind="generation_run", id=chain.generation_run_id),
            BindingRef(kind="label_run", id=chain.judge_run_id),
        ],
    )
    chain.pair_job_id, chain.pair_version_id = job_id, version_id
    await db.commit()


async def _step_counts(db: AsyncSession, version_id: str, operator: str) -> StepExecution | None:
    return (
        await db.execute(
            select(StepExecution)
            .join(VersionStep, VersionStep.step_execution_id == StepExecution.id)
            .where(VersionStep.version_id == version_id, StepExecution.operator_name == operator)
        )
    ).scalar_one_or_none()


def _reasons(execution: StepExecution | None) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in (execution.reason_counts if execution is not None else None) or []:
        if item.get("kind") == "dropped":
            out[str(item["reason_code"])] = int(item["count"])
    return out


async def _complete(db: AsyncSession, chain: MinimalPairChain) -> None:
    """``done``: the pairing step's counts — verified pairs, and every unverified pair by reason
    (FR-009.61: dropped and COUNTED, never kept)."""
    assert chain.pair_version_id is not None
    run = await db.get(GenerationRun, chain.generation_run_id)
    scope = _reasons(
        await _step_counts(db, str(chain.scope_version_id), "minimal_pair_scope")
        if chain.scope_version_id
        else None
    )
    join = await _step_counts(db, str(chain.pair_version_id), "minimal_pair_join")
    dropped = _reasons(join)
    unverified = {r: dropped[r] // 2 for r in PAIR_REASONS if dropped.get(r)}
    verified = int(join.rows_kept or 0) // 2 if join is not None else 0
    chain.counts = {
        "generated": int((run.counts or {}).get("respond:generated", 0)) if run else 0,
        "generation": dict(run.counts or {}) if run else {},
        "scope_dropped": scope,
        "pairs_judged": verified + sum(unverified.values()),
        "pairs_verified": verified,
        "pairs_unverified": sum(unverified.values()),
        "unverified_by_reason": unverified,
    }
    chain.state = "completed"
    chain.stage = "done"
    chain.completed_at = utc_now()
    await db.commit()
    logger.info("minimal_pair_chain.completed chain=%s pairs=%d", chain.id, verified)


async def _version_of(db: AsyncSession, job_id: str) -> str | None:
    build = await db.get(VersionBuild, job_id, populate_existing=True)
    return str(build.version_id) if build is not None and build.version_id else None


async def _move_to(db: AsyncSession, chain: MinimalPairChain, stage: str) -> bool:
    """The previous stage finished: the chain now stands at ``stage`` (committed BEFORE the start,
    so a refused start fails the stage it belongs to)."""
    chain.stage = stage
    await db.commit()
    return True


async def _run_failed(
    db: AsyncSession, chain: MinimalPairChain, state: str, error: Any, what: str
) -> bool:
    """A run or job that failed or was cancelled stops the chain at its stage. A job's error is a
    plain message; a run's is ``{code, message}``."""
    if isinstance(error, str):
        error = {"message": error}
    error = error if isinstance(error, dict) else {}
    await _fail(
        db,
        chain,
        chain.stage,
        str(error.get("code") or f"STAGE_{state.upper()}"),
        str(error.get("message") or f"{what} was {state}."),
        {"ref": what},
    )
    return False


async def _build_stage(db: AsyncSession, chain: MinimalPairChain) -> bool:
    """``scope`` or ``pair``: start the build if it has not started, else watch its job."""
    scope = chain.stage == "scope"
    job_id = chain.scope_job_id if scope else chain.pair_job_id
    version_id = chain.scope_version_id if scope else chain.pair_version_id
    if job_id is None and version_id is None:
        await (_start_scope(db, chain) if scope else _start_pair(db, chain))
        version_id = chain.scope_version_id if scope else chain.pair_version_id
        if version_id is None:
            return False  # the build runs; look again later
    elif version_id is None:
        job = await _job(db, job_id)
        assert job is not None
        if job.status in ("failed", "cancelled"):
            return await _run_failed(db, chain, job.status, job.error, f"build {job.id}")
        if job.status != "completed":
            return False
        version_id = await _version_of(db, str(job_id))
        if version_id is None:
            await _fail(
                db,
                chain,
                chain.stage,
                "BUILD_WROTE_NO_VERSION",
                f"Build {job_id} wrote no version.",
            )
            return False
        if scope:
            chain.scope_version_id = version_id
        else:
            chain.pair_version_id = version_id
        await db.commit()
    if scope:
        return await _move_to(db, chain, "judge")
    await _complete(db, chain)
    return False


async def _step(db: AsyncSession, chain: MinimalPairChain) -> bool:
    """Look at the current stage once. True when the chain moved (look again), False when it
    waits, stopped or finished."""
    if chain.stage == "generate":
        run = await db.get(GenerationRun, chain.generation_run_id, populate_existing=True)
        assert run is not None
        if run.state in ("failed", "cancelled"):
            return await _run_failed(db, chain, run.state, run.error, f"generation run {run.id}")
        if run.state != "completed":
            return False
        return await _move_to(db, chain, "scope")
    if chain.stage in ("scope", "pair"):
        return await _build_stage(db, chain)
    if chain.stage == "judge":
        if chain.judge_run_id is None:
            await _start_judge(db, chain)
            return False
        judge = await db.get(LabelRun, chain.judge_run_id, populate_existing=True)
        assert judge is not None
        if judge.state in ("failed", "cancelled"):
            return await _run_failed(db, chain, judge.state, judge.error, f"judge run {judge.id}")
        if judge.state != "completed":
            return False
        return await _move_to(db, chain, "pair")
    return False


async def advance(db: AsyncSession, chain_id: str) -> MinimalPairChain:
    """Move a running chain as far as its stages allow. Safe to call any time, from anywhere: an
    advisory lock keeps two callers from starting one stage twice."""
    with chain_lock(chain_id) as held:
        chain = await get_chain(db, chain_id)
        if not held:
            return chain
        for _ in range(2 * len(STAGES)):
            if chain.state != "running":
                break
            try:
                moved = await _step(db, chain)
            except (StageRefused, AppError) as refused:
                # A stage that refused to start stops the chain AT that stage, naming it.
                await db.rollback()
                chain = await get_chain(db, chain_id)
                await _fail(
                    db,
                    chain,
                    chain.stage,
                    str(refused.code),
                    str(refused.message),
                    dict(getattr(refused, "details", None) or {}),
                )
                break
            chain = await get_chain(db, chain_id)
            if not moved:
                break
        return chain


# --- operator actions ----------------------------------------------------------------------


async def resume(db: AsyncSession, chain_id: str, who: Who) -> MinimalPairChain:
    """Continue the stage that stopped: its own resume for a run, a new build for a build, a new
    start for a start that was refused. Later stages start as ``who``."""
    from .. import label_run_service

    chain = await get_chain(db, chain_id)
    if not await chain_resumable(db, chain):
        raise ConflictError(
            f"Minimal-pair chain {chain_id} is {chain.state} at {chain.stage}; only a failed or "
            "cancelled chain can be resumed"
            + (
                " (its generation run cannot be resumed: start a new chain)."
                if chain.state in RESUMABLE_CHAIN_STATES
                else "."
            ),
            code="CHAIN_NOT_RESUMABLE",
            details={"state": chain.state, "stage": chain.stage},
        )
    if chain.stage == "generate":
        run = await db.get(GenerationRun, chain.generation_run_id)
        assert run is not None
        if run.state in ("failed", "cancelled"):
            await generation.resume(db, run.id, who)
    elif chain.stage == "judge" and chain.judge_run_id is not None:
        judge = await db.get(LabelRun, chain.judge_run_id)
        if judge is not None and judge.state in ("failed", "cancelled"):
            await label_run_service.resume(db, judge.id, who)
    elif chain.stage in ("scope", "pair"):
        scope = chain.stage == "scope"
        job = await _job(db, chain.scope_job_id if scope else chain.pair_job_id)
        if job is not None and job.status in ("failed", "cancelled"):
            # 002 starts a new job for the same request; the failed one stays in the history.
            if scope:
                chain.scope_job_id = None
            else:
                chain.pair_job_id = None
    chain.state = "running"
    chain.failed_stage = None
    chain.error = None
    chain.acting_by = who.who
    chain.acting_origin = who.origin
    await db.commit()
    logger.info("minimal_pair_chain.resumed chain=%s stage=%s", chain.id, chain.stage)
    return await advance(db, chain_id)


async def cancel(db: AsyncSession, chain_id: str) -> MinimalPairChain:
    """Cancel the live stage through its own owner, then the chain."""
    from .. import label_run_service
    from ..job_service import JobService

    chain = await get_chain(db, chain_id)
    if chain.state != "running":
        raise ConflictError(
            f"Minimal-pair chain {chain_id} is {chain.state}; there is nothing to cancel.",
            code="CHAIN_NOT_CANCELLABLE",
            details={"state": chain.state},
        )
    try:
        if chain.stage == "generate":
            run = await db.get(GenerationRun, chain.generation_run_id)
            if run is not None and run.state not in ("completed", "failed", "cancelled"):
                await generation.cancel(db, run.id)
        elif chain.stage == "judge" and chain.judge_run_id:
            judge = await db.get(LabelRun, chain.judge_run_id)
            if judge is not None and judge.state not in ("completed", "failed", "cancelled"):
                await label_run_service.cancel(db, judge.id)
        elif chain.stage in ("scope", "pair"):
            job = await _job(
                db, chain.scope_job_id if chain.stage == "scope" else chain.pair_job_id
            )
            if job is not None and job.status not in TERMINAL_STATUSES:
                await JobService.cancel(db, job.id, "Cancelled with its minimal-pair chain.")
    except ConflictError:
        pass  # it finished meanwhile; the chain is still cancelled
    chain = await get_chain(db, chain_id)
    chain.state = "cancelled"
    await db.commit()
    return await get_chain(db, chain_id)


async def running_ids(db: AsyncSession) -> list[str]:
    rows = await db.execute(
        select(MinimalPairChain.id)
        .where(MinimalPairChain.state == "running")
        .order_by(MinimalPairChain.updated_at)
    )
    return [str(r) for r in rows.scalars()]
