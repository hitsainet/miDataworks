"""Generation runs: plan, start, cancel, resume, preview, compare, independence, candidate build.

FR-007.1 – FR-007.45; FTDD 007 sections 2.2, 5 and 6.4; FTID 007 sections 3.3 and 5.

**The plan writes nothing.** It resolves the ``generation`` (and ``judge``) roles through 005's one
resolver, reads the input version's lineage, reads every steering profile fresh from miLLM, and
runs the pure guards in this order — held-out first (FR-007.35), seed splits (FR-007.36), steering
support (FR-007.20), profile and SAE existence (FR-007.18), one axis (P-22), judge independence
(T-35, the API call site). Any refusal raises before a row, a file or a job exists.

**The start** re-plans in the same request, then writes the run, its steering snapshots and its
first job in ONE transaction, marks the templates used, and dispatches. ``started_by`` is the
operator's name or the agent's identity (C5, P-12); an empty one is ``NO_IDENTITY``.

``GenerationRun.state`` is written here (API side) and by the engine through :func:`set_state`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from dataclasses import dataclass, field
from typing import Any

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ...clients.endpoint_caller import EndpointCaller
from ...clients.endpoint_errors import EndpointCallError
from ...core.agent_origin import Who
from ...core.canonical_json import canonical_sha256
from ...core.clock import utc_now
from ...core.config import get_settings
from ...core.errors import AppError, ConflictError, NotFoundError, UnprocessableError
from ...core.ids import new_id
from ...core.job_kinds import get_job_kind
from ...models.dataset import Dataset
from ...models.enums import VersionState
from ...models.generation import (
    NOT_RESUMABLE_REASONS,
    RESUMABLE_RUN_STATES,
    TERMINAL_RUN_STATES,
    GenerationRecord,
    GenerationRun,
    GenerationRunJob,
    GenerationTemplate,
    SteeringSnapshot,
)
from ...models.job import TERMINAL_STATUSES, Job
from ...models.version import Version
from ...schemas.generation import (
    CompareOut,
    CompareRequest,
    GenerationRunCreate,
    GenerationRunOut,
    HeldOutStatus,
    IndependenceOut,
    IndependenceRequest,
    PlanOut,
    PreviewItem,
    PreviewOut,
    PreviewRequest,
    SnapshotOut,
)
from .. import label_inputs, server_probe
from ..duck import connect, files_param, quote_ident, top_level_columns
from ..endpoint_resolver import ResolvedRoleEndpoint, RoleUnconfigured, resolve_async
from ..version_read_service import get_version_row
from . import rules, seed_selection, settings_client, steering, template_service
from .generation_call import (
    CallContext,
    CallStop,
    GenerationCall,
    GenerationRequest,
    NativeGenerationCall,
    RelayGenerationCall,
)

logger = logging.getLogger(__name__)

JOB_KIND = "generation_run"
#: Columns each target's generated rows fill; the version must have them (FTDD 007 4.3).
TARGET_COLUMNS: dict[str, tuple[str, ...]] = {
    "sft": ("prompt", "completion"),
    "kto": ("prompt", "completion"),
    "grpo_prompt": ("prompt",),
    "dpo": ("prompt", "completion"),
    "dpo_steered": ("prompt", "chosen", "rejected"),
}


#: A minimal-pair run writes its counterpart into the run's prompt column (009 FR-009.60).
MINIMAL_PAIR_SHAPE = "minimal_pair"


def generated_shape(mode: str, target_type: str) -> str:
    """The row shape a run's generated rows take."""
    if mode == "steered_pairs":
        return "dpo_steered"
    if mode == "minimal_pairs":
        return MINIMAL_PAIR_SHAPE
    return target_type


def shape_columns(shape: str, prompt_column: str) -> tuple[str, ...] | None:
    """The content columns a shape's generated rows fill; None for a shape nothing generates."""
    if shape == MINIMAL_PAIR_SHAPE:
        return (prompt_column,)
    return TARGET_COLUMNS.get(shape)


def caller_for(base_url: str, api_key: str | None, **overrides: Any) -> EndpointCaller:
    settings = get_settings()
    values: dict[str, Any] = {
        "timeout_s": settings.endpoint_http_timeout_seconds,
        "backoff_cap_s": settings.label_backoff_cap_seconds,
        "transient_retries": settings.label_transient_retries,
    }
    values.update(overrides)
    return EndpointCaller(base_url, api_key, **values)


def call_for(path: str) -> GenerationCall:
    """The transport for an engine path; fixed per run (FTID 007 section 3.1)."""
    if path == "relay":
        return RelayGenerationCall()

    def factory(ctx: CallContext) -> EndpointCaller:
        extra: dict[str, Any] = {"timeout_s": ctx.timeout_s}
        if ctx.sleep is not None:
            extra["sleep"] = ctx.sleep
        if ctx.on_wait is not None:
            extra["on_wait"] = ctx.on_wait
        return caller_for(ctx.base_url, ctx.api_key, **extra)

    return NativeGenerationCall(factory)


def _rule_error(exc: rules.GenerationRuleError) -> AppError:
    return AppError(exc.message, code=exc.code, status_code=exc.status, details=exc.details)


def _settings_error(exc: settings_client.SettingsReadError) -> AppError:
    return AppError(exc.message, code=exc.code, status_code=502, details=exc.details)


# --- steering resolution --------------------------------------------------------------------


@dataclass(frozen=True)
class SideSnapshot:
    """One side's resolved steering, before it is written as a ``dw_steering_snapshots`` row."""

    side: str
    effective: rules.EffectiveSet
    sent: list[tuple[int, float]]
    overrides: dict[str, Any]
    #: The set as requested, on its SAE (for the one-axis rule); equal to ``effective`` unless
    #: every strength was zero.
    requested: rules.EffectiveSet | None = None

    @property
    def axis(self) -> rules.EffectiveSet:
        return self.requested or self.effective

    def row(self) -> dict[str, Any]:
        e = self.effective
        values = {
            "side": self.side,
            "kind": e.kind,
            "profile_id": e.profile_id,
            "profile_name": e.profile_name,
            "profile_updated_at": e.profile_updated_at,
            "intensity": e.intensity,
            "model_id": e.model_id,
            "sae_id": e.sae_id,
            "layer": e.layer,
            "features": [[i, s] for i, s in e.applied()],
            "sent_features": [[i, s] for i, s in self.sent],
            "set_hash": e.set_hash(),
            "body_overrides": self.overrides,
        }
        values["snapshot_hash"] = canonical_sha256(values)
        return values

    def out(self) -> SnapshotOut:
        row = self.row()
        return SnapshotOut(**{k: v for k, v in row.items() if k != "body_overrides"})


def _probe(resolved: ResolvedRoleEndpoint) -> server_probe.ServerInfo:
    with caller_for(resolved.base_url, resolved.api_key, transient_retries=0) as caller:
        return server_probe.detect_server(caller)


def resolve_side(
    side: str,
    setting: dict[str, Any],
    resolved: ResolvedRoleEndpoint,
    server: server_probe.ServerInfo,
    steering_supported: bool,
) -> SideSnapshot:
    """A setting → its snapshot, reading miLLM's profile and SAE attachments (read-only).

    ``STEERING_UNSUPPORTED`` for any steering while 028 is unmet or the endpoint is not miLLM;
    ``PROFILE_NOT_FOUND`` / ``SAE_NOT_ATTACHED`` when miLLM lacks them — nothing is created or
    attached (FR-007.18)."""
    kind = setting.get("kind", "none")
    if kind != "none" and not (server.is_millm and steering_supported):
        raise ConflictError(
            "Steering needs miLLM serving inline steering and X-miLLM-Steering (miLLM feature "
            "028)"
            + ("" if server.is_millm else f"; the generation endpoint is {server.kind}, not miLLM")
            + ". Generate unsteered, or point the generation role at miLLM.",
            code="STEERING_UNSUPPORTED",
            details={"server_kind": server.kind, "steering_supported": steering_supported},
        )
    profile: dict[str, Any] | None = None
    attached: list[settings_client.Attachment] = []
    if kind != "none":
        with caller_for(resolved.base_url, resolved.api_key, transient_retries=0) as caller:
            try:
                if kind == "profile":
                    profile = settings_client.profile_by_name(caller, str(setting["profile_name"]))
                attached = settings_client.attachments(caller)
            except settings_client.SettingsReadError as exc:
                raise _settings_error(exc) from None
            except EndpointCallError as exc:
                raise AppError(exc.message, code=exc.code, status_code=502) from None
        if kind == "profile" and profile is None:
            raise ConflictError(
                f"miLLM has no profile named {setting['profile_name']!r}. Create it in miLLM "
                "first; miDataworks never creates or changes a profile.",
                code="PROFILE_NOT_FOUND",
                details={"profile_name": setting["profile_name"]},
            )
    effective = rules.resolve_effective_set(setting, profile)
    if kind != "none":
        wanted = effective.sae_id
        matches = [a for a in attached if wanted is None or a.sae_id == wanted]
        if wanted is None and len(matches) != 1:
            matches = []
        if not matches:
            raise ConflictError(
                f"The SAE {wanted or '(the profile names none)'} is not attached in miLLM. Attach "
                "it in miLLM first; miDataworks never attaches an SAE.",
                code="SAE_NOT_ATTACHED",
                details={"sae_id": wanted, "attached": [a.sae_id for a in attached]},
            )
        attachment = matches[0]
        effective = rules.EffectiveSet(
            effective.kind,
            model_id=effective.model_id or resolved.model_id,
            sae_id=attachment.sae_id,
            layer=attachment.layer,
            features=effective.features,
            profile_id=effective.profile_id,
            profile_name=effective.profile_name,
            profile_updated_at=effective.profile_updated_at,
            intensity=effective.intensity,
        )
    else:
        effective = rules.EffectiveSet("none", model_id=resolved.model_id)
    # What the request asked for, zeros included (the record keeps it as sent).
    sent = (
        [(int(f["index"]), float(f["strength"])) for f in setting.get("features") or []]
        if kind == "inline"
        else effective.sorted_pairs()
    )
    requested = effective
    if effective.kind != "none" and not effective.applied():
        # Every strength is zero: miLLM applies nothing and reports `none`, and it REFUSES an
        # `sae_id` beside an empty feature list (miLLM FR-28.2.2, schemas/openai.py). Such a side
        # is the explicitly unsteered side; the one-axis rule still sees its index at 0.
        effective = rules.EffectiveSet("none", model_id=effective.model_id)
    overrides = rules.body_overrides(
        effective.kind,
        profile_name=effective.profile_name,
        sae_id=effective.sae_id,
        features=sent,
        steering_supported=steering_supported,
        is_millm=server.is_millm,
    )
    return SideSnapshot(side, effective, sent, overrides, requested)


def expected_for(
    snapshot: SteeringSnapshot | SideSnapshot, is_millm: bool
) -> steering.Expected | None:
    """What a response must report for this side; None when the endpoint has no contract."""
    if not is_millm:
        return None
    if isinstance(snapshot, SideSnapshot):
        return snapshot.effective.expected()
    return steering.expected_from(
        snapshot.kind,
        profile_name=snapshot.profile_name,
        intensity=snapshot.intensity,
        sae_id=snapshot.sae_id,
        layer=snapshot.layer,
        features=snapshot.features,
        set_hash=snapshot.set_hash,
    )


# --- the plan -------------------------------------------------------------------------------


@dataclass
class Planned:
    out: PlanOut
    version: Version
    resolved: ResolvedRoleEndpoint
    server: server_probe.ServerInfo
    sides: list[SideSnapshot]
    held_out: list[str]
    expand: GenerationTemplate | None
    respond: GenerationTemplate | None
    revision: str | None
    required_model: str | None
    judge_identity: rules.Identity | None
    generator_identities: list[rules.Identity]
    warnings: list[dict[str, Any]] = field(default_factory=list)


async def _template(
    db: AsyncSession, template_id: str | None, kind: str, columns: set[str], field_name: str
) -> GenerationTemplate | None:
    if template_id is None:
        return None
    row = await template_service.get(db, template_id)
    if row.kind != kind:
        raise UnprocessableError(
            f"Template {row.name}@{row.version} is a {row.kind} template; {field_name} needs a "
            f"{kind} template.",
            code="TEMPLATE_KIND_MISMATCH",
            details={"field": field_name, "kind": row.kind},
        )
    template_service.check_placeholders(row, columns, field=field_name)
    return row


def _columns(files: list[Any]) -> list[str]:
    con = connect()
    try:
        return top_level_columns(con, files)
    finally:
        con.close()


def count_seed_rows(files: list[Any], seed_splits: list[str]) -> int:
    """Distinct SOURCE rows in the seed splits (FR-007.36): generated rows are never seeds."""
    con = connect()
    try:
        columns = top_level_columns(con, files)
        key = quote_ident("_dw_row_key", columns)
        origin = quote_ident("_dw_origin", columns)
        split = quote_ident("_dw_split", columns)
        placeholders = ", ".join("?" for _ in seed_splits)
        row = con.execute(
            f"SELECT count(DISTINCT {key}) FROM read_parquet(?) "  # noqa: S608 - quoted
            f"WHERE {origin} = 'source' AND {split} IN ({placeholders})",
            [files_param(files), *seed_splits],
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        con.close()


async def _judge_identity(
    db: AsyncSession, gen_server: server_probe.ServerInfo, resolved: ResolvedRoleEndpoint
) -> tuple[rules.Identity | None, str | None]:
    """The judge's identity (model, revision or "not reported", steering "none"), or None when
    no judge is configured yet (independence is then checked again at label time, FR-007.25)."""
    try:
        judge = await resolve_async(db, "judge")
    except RoleUnconfigured:
        return None, None
    server = gen_server
    if judge.base_url != resolved.base_url:
        try:
            server = await run_in_threadpool(_probe, judge)
        except EndpointCallError:
            return rules.generator_identity(judge.model_id, None, None), judge.inherited_from
    revision = server_probe.model_revision(server, judge.model_id)
    return rules.generator_identity(judge.model_id, revision, None), judge.inherited_from


def _known_seed_splits(version: Version, seed_splits: list[str]) -> None:
    split_names = {str(s["name"]) for s in version.splits}
    unknown = sorted(set(seed_splits) - split_names)
    if unknown:
        raise UnprocessableError(
            f"The version has no split(s) {unknown}; it has {sorted(split_names)}.",
            code="SEED_SPLIT_UNKNOWN",
            details={"unknown": unknown},
        )


def _copies_warning_from(found: dict[str, Any]) -> dict[str, Any] | None:
    """A plan warning when seed rows share a row key and a column the run reads differs between
    the copies: the run seeds one copy per key, so the other copies' values are never sent."""
    if not found["keys_affected"]:
        return None
    names = sorted(found["columns"])
    return {
        "code": "seed_copies_disagree",
        "message": (
            f"{found['keys_affected']:,} of {found['keys']:,} seed row keys have copies whose "
            f"{', '.join(names)} differ ({found['rows']:,} source rows in all). A row's key is "
            "computed from its content columns only, and the run uses ONE copy per row key (the "
            "first occurrence), so the other copies' values are never sent. To generate from "
            "every copy, make the rows distinct: give each row an identifying content column."
        ),
        "keys_affected": found["keys_affected"],
        "keys": found["keys"],
        "rows": found["rows"],
        "columns": found["columns"],
    }


async def plan(db: AsyncSession, req: GenerationRunCreate, origin: str) -> Planned:
    settings = get_settings()
    version = await get_version_row(db, req.input_version_id)
    if version.state != VersionState.COMPLETED:
        raise ConflictError(
            f"Version {req.input_version_id} is {version.state}; generate from a completed one.",
            code="VERSION_NOT_USABLE",
        )
    # 1. held-out first (FR-007.35), then seed splits (FR-007.36) — the API call sites.
    try:
        held_out = rules.held_out_guard(version.splits, version.held_out_origin_version_id)
        rules.seed_split_guard(req.seed_splits, held_out)
    except rules.GenerationRuleError as exc:
        raise _rule_error(exc) from None
    _known_seed_splits(version, req.seed_splits)
    dataset = await db.get(Dataset, version.dataset_id)
    assert dataset is not None
    if dataset.target_type != req.target_type:
        raise UnprocessableError(
            f"The version's dataset is a {dataset.target_type} dataset; this run makes "
            f"{req.target_type} rows. Generate into a dataset of that target type.",
            code="TARGET_TYPE_MISMATCH",
            details={"dataset_target_type": dataset.target_type},
        )
    files = label_inputs.version_files(version.splits)
    columns = set(await run_in_threadpool(_columns, files))
    if req.prompt_column not in columns:
        raise UnprocessableError(
            f"The version has no column {req.prompt_column!r}.",
            code="PROMPT_COLUMN_UNKNOWN",
            details={"columns": sorted(c for c in columns if not c.startswith("_dw_"))},
        )
    shape = generated_shape(req.mode, req.target_type)
    fills = shape_columns(shape, req.prompt_column)
    if fills is None:
        # Unreachable through the schema (its target types all have a shape); kept so a new
        # target type cannot reach TARGET_COLUMNS[shape] as a KeyError.
        raise UnprocessableError(
            f"Generation makes {sorted(TARGET_COLUMNS)} rows, or detector minimal pairs; not "
            f"{req.target_type}.",
            code="TARGET_TYPE_UNSUPPORTED",
            details={"target_type": req.target_type, "mode": req.mode},
        )
    content = {c for c, r in version.column_roles.items() if r == "content"}
    missing = [c for c in fills if c not in content]
    if missing:
        raise UnprocessableError(
            f"Generated {shape} rows fill {list(fills)}; the version lacks "
            f"{missing} as content column(s).",
            code="TARGET_COLUMNS_MISSING",
            details={"missing": missing, "content_columns": sorted(content)},
        )
    if req.target_type == "grpo_prompt" and req.respond_template_id is not None:
        raise UnprocessableError(
            "A grpo_prompt run makes prompts only: name an expand template and no respond "
            "template (FR-007.9).",
            code="PROMPT_ONLY_RUN",
        )
    if req.target_type != "grpo_prompt" and req.respond_template_id is None:
        raise UnprocessableError(
            f"A {req.target_type} run needs a respond template.", code="RESPOND_TEMPLATE_REQUIRED"
        )
    await template_service.ensure_builtins(db)
    expand = await _template(db, req.expand_template_id, "expand", columns, "expand_template_id")
    respond = await _template(
        db, req.respond_template_id, "respond", columns, "respond_template_id"
    )
    if req.n_responses > settings.generation_max_n_responses:
        raise UnprocessableError(
            f"At most {settings.generation_max_n_responses} responses per prompt.",
            code="N_RESPONSES_TOO_LARGE",
        )
    resolved = await resolve_async(db, "generation")  # ROLE_UNCONFIGURED
    try:
        server = await run_in_threadpool(_probe, resolved)
    except EndpointCallError as exc:
        raise AppError(exc.message, code=exc.code, status_code=502) from None
    required_model: str | None = None
    if server.is_millm:
        required_model = resolved.model_id
        resident = server.resident.name if server.resident else None
        if resident != resolved.model_id and not await _active_model_jobs(db, resident or ""):
            raise ConflictError(
                f"{resolved.model_id} is not loaded in miLLM ({resident or 'nothing'} is). Load "
                f"{resolved.model_id} in miLLM, then start the run; miDataworks never loads one.",
                code="MODEL_NOT_LOADED",
                details={"needed": resolved.model_id, "resident": resident},
            )
        lease = server.lease or {}
        holder = lease.get("holder")
        if holder and holder != settings.millm_lease_holder:
            raise ConflictError(
                f"miLLM's model is leased by {holder} until {lease.get('expires_at')}. Start the "
                "run when that lease ends.",
                code="LEASE_HELD",
                details={"holder": holder, "expires_at": lease.get("expires_at")},
            )
    supported = bool(settings.millm_steering_supported)
    if req.mode == "steered_pairs":
        assert req.setting_a is not None and req.setting_b is not None
        side_a = await run_in_threadpool(
            resolve_side, "a", req.setting_a.model_dump(), resolved, server, supported
        )
        side_b = await run_in_threadpool(
            resolve_side, "b", req.setting_b.model_dump(), resolved, server, supported
        )
        if not (server.is_millm and supported):
            raise ConflictError(
                "Steered pairs need miLLM serving inline steering and X-miLLM-Steering.",
                code="STEERING_UNSUPPORTED",
                details={"server_kind": server.kind, "steering_supported": supported},
            )
        try:
            differing: int | None = rules.one_axis_or_raise(side_a.axis, side_b.axis)
        except rules.GenerationRuleError as exc:
            raise _rule_error(exc) from None
        generator = await run_in_threadpool(
            resolve_side, "generator", {"kind": "none"}, resolved, server, supported
        )
        sides = [generator, side_a, side_b]
        generation_sides = [side_a, side_b]
    else:
        generator = await run_in_threadpool(
            resolve_side,
            "generator",
            req.generator_setting.model_dump(),
            resolved,
            server,
            supported,
        )
        sides = [generator]
        generation_sides = [generator]
        differing = None
    available = await run_in_threadpool(count_seed_rows, files, list(req.seed_splits))
    if available == 0:
        raise UnprocessableError(
            "The seed splits hold no source rows to generate from.",
            code="NO_SEED_ROWS",
            details={"seed_splits": list(req.seed_splits)},
        )
    selected = min(req.sample_size, available)
    read_columns = seed_selection.needed_columns(
        req.prompt_column, [t.body if t else None for t in (expand, respond)]
    )
    copies = await run_in_threadpool(
        seed_selection.copies_disagree, list(version.splits), list(req.seed_splits), read_columns
    )
    revision = server_probe.model_revision(server, resolved.model_id)
    generator_ids = [
        rules.generator_identity(resolved.model_id, revision, s.effective.set_hash())
        for s in generation_sides
    ]
    judge, inherited = await _judge_identity(db, server, resolved)
    if judge is not None:
        # The API call site of the judge-independence rule (FR-007.25; T-35).
        conflicts = rules.judge_conflicts(judge, generator_ids)
        if conflicts:
            raise _rule_error(
                rules.conflict_error(judge, conflicts, inherited_from=resolved.inherited_from)
            )
    warnings: list[dict[str, Any]] = []
    if judge is None:
        warnings.append(
            {
                "code": "judge_unconfigured",
                "message": "No judge is configured yet; judge independence is checked again when "
                "a label run is started over the generated rows.",
            }
        )
    if not server.is_millm:
        warnings.append(
            {
                "code": "steering_not_applicable",
                "message": f"The generation endpoint is {server.kind}: it reports no steering "
                "state, so each record says 'not applicable', never 'unsteered'.",
            }
        )
    elif not supported:
        warnings.append(
            {
                "code": "steering_not_reported",
                "message": "miLLM does not report steering yet (feature 028): unsteered records "
                "say 'not reported'.",
            }
        )
    copies_warning = _copies_warning_from(copies)
    if copies_warning is not None:
        warnings.append(copies_warning)
    if revision is None:
        warnings.append(
            {
                "code": "revision_not_reported",
                "message": "The endpoint reports no model revision; the generator identity says "
                "'not reported' (P-13).",
            }
        )
    stages = ["seed"] + (["expand"] if expand else []) + (["respond"] if respond else [])
    sides_per_response = 2 if req.mode == "steered_pairs" else 1
    requests = (selected if expand else 0) + (
        selected * req.n_responses * sides_per_response if respond else 0
    )
    out = PlanOut(
        mode=req.mode,
        target_type=req.target_type,
        held_out=HeldOutStatus(
            present=True, splits=held_out, origin_version_id=version.held_out_origin_version_id
        ),
        seed_rows_available=available,
        seed_rows_selected=selected,
        responses_per_prompt=req.n_responses,
        expected_requests=requests,
        stages=stages,
        engine_path=settings.generation_engine_path,
        server_kind=server.kind,
        resident_model=server.resident.name if server.resident else None,
        model_revision=revision,
        generation_endpoint=resolved.snapshot(),
        snapshots=[s.out() for s in sides],
        differing_index=differing,
        generator_identities=[i.as_dict() for i in generator_ids],
        judge_identity=judge.as_dict() if judge else None,
        independence="independent" if judge is not None else "not_checked",
        pinned_expected=(server.lease_supported if server.is_millm else False),
        warnings=warnings,
    )
    return Planned(
        out=out,
        version=version,
        resolved=resolved,
        server=server,
        sides=sides,
        held_out=held_out,
        expand=expand,
        respond=respond,
        revision=revision,
        required_model=required_model,
        judge_identity=judge,
        generator_identities=generator_ids,
        warnings=warnings,
    )


async def _active_model_jobs(db: AsyncSession, model: str) -> bool:
    found = (
        await db.execute(
            select(func.count())
            .select_from(Job)
            .where(Job.required_model_id == model, Job.status.in_(("queued", "running")))
        )
    ).scalar_one()
    return bool(found)


# --- start ----------------------------------------------------------------------------------


def _dispatch() -> list[str]:
    from ...core.database import get_sync_db
    from ..job_service import dispatch_queued

    with get_sync_db() as session:
        return dispatch_queued(session)


def _new_job(run: GenerationRun, who: Who, required_model: str | None) -> Job:
    get_job_kind(JOB_KIND)
    return Job(
        id=new_id("job"),
        kind=JOB_KIND,
        status="queued",
        progress=0.0,
        params={"generation_run_id": run.id},
        started_by=who.who,
        started_by_origin=who.origin,
        required_model_id=required_model,
    )


async def start(
    db: AsyncSession, req: GenerationRunCreate, planned: Planned, who: Who
) -> GenerationRun:
    if not who.who.strip():
        raise AppError("A run needs who started it.", code="NO_IDENTITY", status_code=422)
    settings = get_settings()
    seed = req.seed if req.seed is not None else secrets.randbelow(2_147_483_648)
    judge = planned.judge_identity
    run = GenerationRun(
        id=new_id("gr"),
        mode=req.mode,
        target_type=req.target_type,
        input_version_id=planned.version.id,
        held_out_origin_version_id=planned.version.held_out_origin_version_id,
        held_out_splits=planned.held_out,
        prompt_column=req.prompt_column,
        seed_splits=list(req.seed_splits),
        sample_size=req.sample_size,
        seed=seed,
        n_responses=req.n_responses,
        expand_template_id=planned.expand.id if planned.expand else None,
        respond_template_id=planned.respond.id if planned.respond else None,
        stages=planned.out.stages,
        generation_endpoint=planned.resolved.snapshot(),
        server_kind=planned.server.kind,
        generator_identities=[i.as_dict() for i in planned.generator_identities],
        judge_identity=judge.as_dict() if judge else None,
        judge_identity_hash=canonical_sha256(judge.as_dict()) if judge else None,
        independence_checked_at=utc_now() if judge else None,
        chosen_side=req.chosen_side,
        engine_path=settings.generation_engine_path,
        steering_supported=bool(settings.millm_steering_supported),
        model_revision=planned.revision,
        revision_reported=planned.revision is not None,
        state="queued",
        warnings=list(planned.warnings),
        counts={},
        started_by=who.who,
        started_by_origin=who.origin,
    )
    db.add(run)
    await db.flush()
    for side in planned.sides:
        db.add(SteeringSnapshot(run_id=run.id, **side.row()))
    for template in (planned.expand, planned.respond):
        if template is not None:
            template_service.mark_used(template)
    job = _new_job(run, who, planned.required_model)
    db.add(job)
    await db.flush()
    db.add(GenerationRunJob(run_id=run.id, seq=0, job_id=job.id))
    await db.commit()
    logger.info(
        "generation_run.started run=%s job=%s mode=%s seeds=%d origin=%s",
        run.id,
        job.id,
        run.mode,
        planned.out.seed_rows_selected,
        who.origin,
    )
    await run_in_threadpool(_dispatch)
    return run


# --- lifecycle ------------------------------------------------------------------------------


async def get_run(db: AsyncSession, run_id: str) -> GenerationRun:
    run = await db.get(GenerationRun, run_id, populate_existing=True)
    if run is None:
        raise NotFoundError(f"No generation run {run_id}.", code="GENERATION_RUN_NOT_FOUND")
    return run


async def _jobs(db: AsyncSession, run_id: str) -> list[Job]:
    rows = await db.execute(
        select(Job)
        .join(GenerationRunJob, GenerationRunJob.job_id == Job.id)
        .where(GenerationRunJob.run_id == run_id)
        .order_by(GenerationRunJob.seq)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars())


async def cancel(db: AsyncSession, run_id: str) -> GenerationRun:
    from ..job_service import JobService

    run = await get_run(db, run_id)
    live = [j for j in await _jobs(db, run_id) if j.status not in TERMINAL_STATUSES]
    if not live:
        raise ConflictError(
            f"Generation run {run_id} is {run.state}; there is nothing to cancel.",
            code="RUN_NOT_CANCELLABLE",
            details={"state": run.state},
        )
    job, _ = await JobService.cancel(db, live[-1].id, "Cancelled by the operator.")
    if job.status == "cancelled":  # never started: the worker will not mark it
        run = await get_run(db, run_id)
        run.state = "cancelled"
        run.completed_at = utc_now()
        await db.commit()
    return await get_run(db, run_id)


def resumable(run: GenerationRun) -> bool:
    return run.state in RESUMABLE_RUN_STATES and run.failure_reason not in NOT_RESUMABLE_REASONS


async def resume(db: AsyncSession, run_id: str, who: Who) -> GenerationRun:
    """A new job continues the same run (ADR-007). Refused for a completed run and for a run that
    failed because a profile changed: the setting moved, so start a new run (FR-007.17)."""
    run = await get_run(db, run_id)
    jobs = await _jobs(db, run_id)
    if not resumable(run) or any(j.status not in TERMINAL_STATUSES for j in jobs):
        advice = (
            " A profile changed under the run; start a new run with the profile as it is now."
            if run.failure_reason in NOT_RESUMABLE_REASONS
            else " Only a cancelled or failed run can be resumed."
        )
        raise ConflictError(
            f"Generation run {run_id} is {run.state}.{advice}",
            code="RUN_NOT_RESUMABLE",
            details={"state": run.state, "failure_reason": run.failure_reason},
        )
    required = run.generation_endpoint.get("model_id") if run.server_kind == "millm" else None
    job = _new_job(run, who, required)
    db.add(job)
    await db.flush()
    db.add(GenerationRunJob(run_id=run.id, seq=len(jobs), job_id=job.id))
    run.state = "queued"
    run.error = None
    run.failure_reason = None
    run.completed_at = None
    await db.commit()
    await run_in_threadpool(_dispatch)
    return await get_run(db, run_id)


def set_state(
    session: Session,
    run: GenerationRun,
    state: str,
    *,
    error: dict[str, Any] | None = None,
    failure_reason: str | None = None,
) -> None:
    """The ONE writer of ``GenerationRun.state`` on the worker side."""
    run.state = state
    if error is not None:
        run.error = error
    if failure_reason is not None:
        run.failure_reason = failure_reason
    if state in TERMINAL_RUN_STATES:
        run.completed_at = utc_now()
    session.commit()


# --- reads ----------------------------------------------------------------------------------


def snapshot_out(row: SteeringSnapshot) -> SnapshotOut:
    return SnapshotOut(
        side=row.side,
        kind=row.kind,
        profile_id=row.profile_id,
        profile_name=row.profile_name,
        profile_updated_at=row.profile_updated_at,
        intensity=row.intensity,
        model_id=row.model_id,
        sae_id=row.sae_id,
        layer=row.layer,
        features=row.features,
        sent_features=row.sent_features,
        set_hash=row.set_hash,
        snapshot_hash=row.snapshot_hash,
    )


async def run_out(db: AsyncSession, run: GenerationRun) -> GenerationRunOut:
    jobs = await _jobs(db, run.id)
    snaps = (
        await db.execute(
            select(SteeringSnapshot)
            .where(SteeringSnapshot.run_id == run.id)
            .order_by(SteeringSnapshot.side)
        )
    ).scalars()
    current = next((j.id for j in reversed(jobs) if j.status not in TERMINAL_STATUSES), None)
    return GenerationRunOut(
        id=run.id,
        mode=run.mode,
        target_type=run.target_type,
        state=run.state,
        input_version_id=str(run.input_version_id),
        held_out_origin_version_id=str(run.held_out_origin_version_id),
        held_out_splits=list(run.held_out_splits),
        prompt_column=run.prompt_column,
        seed_splits=list(run.seed_splits),
        sample_size=run.sample_size,
        seed=int(run.seed),
        n_responses=int(run.n_responses),
        expand_template_id=run.expand_template_id,
        respond_template_id=run.respond_template_id,
        stages=list(run.stages),
        generation_endpoint=run.generation_endpoint,
        server_kind=run.server_kind,
        generator_identities=list(run.generator_identities),
        judge_identity=run.judge_identity,
        independence_checked_at=run.independence_checked_at,
        chosen_side=run.chosen_side,
        engine_path=run.engine_path,
        steering_supported=run.steering_supported,
        pinned=run.pinned,
        revision_reported=run.revision_reported,
        model_revision=run.model_revision,
        failure_reason=run.failure_reason,
        error=run.error,
        warnings=list(run.warnings),
        counts=dict(run.counts),
        snapshots=[snapshot_out(s) for s in snaps],
        started_by=run.started_by,
        started_by_origin=run.started_by_origin,
        created_at=run.created_at,
        completed_at=run.completed_at,
        job_ids=[j.id for j in jobs],
        current_job_id=current,
        room=get_job_kind(JOB_KIND).room(run.id),
        resumable=resumable(run) and current is None,
    )


async def list_runs(
    db: AsyncSession,
    *,
    input_version_id: str | None,
    state: str | None,
    mode: str | None,
    page: int,
    limit: int,
) -> tuple[list[GenerationRun], int]:
    query = select(GenerationRun)
    if input_version_id:
        query = query.where(GenerationRun.input_version_id == input_version_id)
    if state:
        query = query.where(GenerationRun.state == state)
    if mode:
        query = query.where(GenerationRun.mode == mode)
    total = int((await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one())
    rows = await db.execute(
        query.order_by(GenerationRun.created_at.desc(), GenerationRun.id)
        .offset((page - 1) * limit)
        .limit(limit)
    )
    return list(rows.scalars()), total


async def binding_record(db: AsyncSession, run_id: str) -> dict[str, Any] | None:
    """Feature 002's binding resolver for ``generation_run`` (FR-002.2, C-002.8)."""
    run = await db.get(GenerationRun, run_id)
    if run is None:
        return None
    rows = int(
        (
            await db.execute(
                select(func.count())
                .select_from(GenerationRecord)
                .where(GenerationRecord.run_id == run_id, GenerationRecord.outcome == "generated")
            )
        ).scalar_one()
    )
    return {"complete": run.state == "completed", "rows": rows}


# --- compare and independence ---------------------------------------------------------------


async def _endpoint(db: AsyncSession) -> tuple[ResolvedRoleEndpoint, server_probe.ServerInfo]:
    resolved = await resolve_async(db, "generation")
    try:
        server = await run_in_threadpool(_probe, resolved)
    except EndpointCallError as exc:
        raise AppError(exc.message, code=exc.code, status_code=502) from None
    return resolved, server


async def compare(db: AsyncSession, req: CompareRequest) -> CompareOut:
    """Resolve two settings and report the indices that differ (FR-007.15). A difference on zero
    or several indices is an ANSWER here (``one_axis: false``), not an error: the form asks while
    the operator types. The run's plan refuses it."""
    resolved, server = await _endpoint(db)
    supported = bool(get_settings().millm_steering_supported)
    a = await run_in_threadpool(
        resolve_side, "a", req.setting_a.model_dump(), resolved, server, supported
    )
    b = await run_in_threadpool(
        resolve_side, "b", req.setting_b.model_dump(), resolved, server, supported
    )
    snapshots = [a.out(), b.out()]
    try:
        index = rules.one_axis_or_raise(a.axis, b.axis)
    except rules.GenerationRuleError as exc:
        return CompareOut(
            one_axis=False,
            differing=list(exc.details.get("differing", [])),
            differing_index=None,
            not_comparable=list(exc.details.get("not_comparable", [])),
            snapshots=snapshots,
            message=exc.message,
            code=exc.code,
        )
    va, vb = a.axis.features.get(index, 0.0), b.axis.features.get(index, 0.0)
    return CompareOut(
        one_axis=True,
        differing=[{"index": index, "a": va, "b": vb}],
        differing_index=index,
        not_comparable=[],
        snapshots=snapshots,
        message=f"Differs on feature {index} only: {va:g} vs {vb:g}.",
        code=None,
    )


async def independence(db: AsyncSession, req: IndependenceRequest) -> IndependenceOut:
    """The judge's identity beside the generator identities, with any conflict (FR-007.24)."""
    resolved, server = await _endpoint(db)
    supported = bool(get_settings().millm_steering_supported)
    if req.mode == "steered_pairs":
        if req.setting_a is None or req.setting_b is None:
            raise UnprocessableError(
                "Steered pairs need setting_a and setting_b.", code="VALIDATION_ERROR"
            )
        settings_list = [req.setting_a.model_dump(), req.setting_b.model_dump()]
    else:
        settings_list = [req.generator_setting.model_dump()]
    sides = [
        await run_in_threadpool(resolve_side, f"s{i}", s, resolved, server, supported)
        for i, s in enumerate(settings_list)
    ]
    revision = server_probe.model_revision(server, resolved.model_id)
    generators = [
        rules.generator_identity(resolved.model_id, revision, s.effective.set_hash()) for s in sides
    ]
    judge, _ = await _judge_identity(db, server, resolved)
    if judge is None:
        return IndependenceOut(
            independent=None,
            judge_identity=None,
            generator_identities=[g.as_dict() for g in generators],
            conflicts=[],
            inherited_from=resolved.inherited_from,
            message="No judge is configured; configure one in Settings → Endpoints → judge.",
        )
    conflicts = rules.judge_conflicts(judge, generators)
    message = (
        rules.conflict_error(judge, conflicts, inherited_from=resolved.inherited_from).message
        if conflicts
        else "The judge is a different model from every generator setting."
    )
    return IndependenceOut(
        independent=not conflicts,
        judge_identity=judge.as_dict(),
        generator_identities=[g.as_dict() for g in generators],
        conflicts=conflicts,
        inherited_from=resolved.inherited_from,
        message=message,
    )


# --- preview --------------------------------------------------------------------------------


@dataclass(frozen=True)
class PreviewSeed:
    """One preview input: the prompt, the values every placeholder renders from, and the seed
    row's key (``None`` for a bare prompt)."""

    prompt: str
    values: dict[str, Any]
    row_key: str | None = None


def _preview_rows(
    splits: list[Any],
    seed_splits: list[str],
    held_out: list[str],
    prompt_column: str,
    needed: list[str],
    n: int,
    selection_seed: int,
) -> list[PreviewSeed]:
    """Seed rows for a preview, chosen exactly as a run chooses them (same candidates, same
    selection, same per-row re-check)."""
    candidates = seed_selection.seed_candidates(splits, seed_splits, needed)
    rows = seed_selection.select_seed_rows(candidates, needed, prompt_column, n, selection_seed)
    seed_selection.check_seed_rows(rows, seed_splits, held_out)
    return [
        PreviewSeed(str(r["prompt"] or ""), json.loads(r["values"] or "{}"), str(r["row_key"]))
        for r in rows
    ]


def _preview_sync(
    resolved: ResolvedRoleEndpoint,
    server: server_probe.ServerInfo,
    side: SideSnapshot,
    template: dict[str, Any] | None,
    seeds: list[PreviewSeed],
    seed: int | None,
    path: str,
    deadline_s: float,
) -> tuple[list[PreviewItem], bool]:
    import time

    begin = time.monotonic()

    def on_wait(seconds: float, reason: str) -> None:
        if time.monotonic() + seconds - begin > deadline_s:
            raise CallStop(
                "PREVIEW_TIMEOUT", f"The endpoint is busy ({reason}); try again shortly."
            )

    body = template or {"prompt": "{prompt}", "sampling": {}, "system": None}
    expected = expected_for(side, server.is_millm)
    call = call_for(path)
    items: list[PreviewItem] = []
    stopped = False
    for i, one in enumerate(seeds):
        if time.monotonic() - begin > deadline_s:
            stopped = True
            break
        # the run's own rendering (run_engine._messages): the row's values, then the prompt
        messages = seed_selection.render_messages(body, {**one.values, "prompt": one.prompt})
        req_seed = None if seed is None else rules.response_seed(seed, f"preview:{i}", 0)
        request = GenerationRequest(
            record_index=i,
            row_key=one.row_key or f"preview-{i}",
            messages=messages,
            sampling=dict(body.get("sampling") or {}),
            seed=req_seed,
        )
        ctx = CallContext(
            base_url=resolved.base_url,
            model=resolved.model_id,
            api_key=resolved.api_key,
            lease_id=None,  # a preview never takes a lease (005's sample precedent)
            is_millm=server.is_millm,
            body_overrides=side.overrides,
            on_wait=on_wait,
            timeout_s=deadline_s,
        )
        (result,) = call.run([request], ctx)
        check = steering.check_reported_state(
            expected, steering.parse_steering_header(result.steering_header)
        )
        items.append(
            PreviewItem(
                prompt=one.prompt,
                row_key=one.row_key,
                text=result.text,
                model=result.model,
                finish_reason=result.finish_reason,
                reported_steering=result.steering_header,
                steering_check=check.result,
                check_reasons=list(check.reasons),
                seed_sent=req_seed,
                seed_confirmed=(None if result.seed_echo is None else result.seed_echo == req_seed),
                latency_ms=result.latency_ms,
                error=result.error,
            )
        )
    return items, stopped


async def _preview_seeds(
    db: AsyncSession, req: PreviewRequest, template: GenerationTemplate | None, limit: int
) -> tuple[list[PreviewSeed], int | None]:
    """The preview's inputs. Bare prompts are refused when the template reads any placeholder
    other than ``{prompt}``: rendering it empty would show something no run sends."""
    body = dict(template.body) if template is not None else None
    if req.input_version_id is None:
        assert req.prompts is not None
        missing = seed_selection.template_placeholders([body])
        if missing:
            raise UnprocessableError(
                f"The template reads {missing}, which a bare prompt does not have; a preview "
                "never renders a placeholder empty. Preview from seed rows instead: give "
                "input_version_id, prompt_column and seed_splits.",
                code="PREVIEW_NEEDS_ROWS",
                details={"missing": missing},
            )
        return [PreviewSeed(p, {}) for p in req.prompts], None
    assert req.prompt_column is not None and req.seed_splits is not None
    version = await get_version_row(db, req.input_version_id)
    if version.state != VersionState.COMPLETED:
        raise ConflictError(
            f"Version {req.input_version_id} is {version.state}; preview from a completed one.",
            code="VERSION_NOT_USABLE",
        )
    try:
        held_out = rules.held_out_guard(version.splits, version.held_out_origin_version_id)
        rules.seed_split_guard(req.seed_splits, held_out)
    except rules.GenerationRuleError as exc:
        raise _rule_error(exc) from None
    _known_seed_splits(version, list(req.seed_splits))
    files = label_inputs.version_files(version.splits)
    columns = set(await run_in_threadpool(_columns, files))
    if req.prompt_column not in columns:
        raise UnprocessableError(
            f"The version has no column {req.prompt_column!r}.",
            code="PROMPT_COLUMN_UNKNOWN",
            details={"columns": sorted(c for c in columns if not c.startswith("_dw_"))},
        )
    if template is not None:
        template_service.check_placeholders(template, columns, field="respond_template_id")
    needed = seed_selection.needed_columns(req.prompt_column, [body])
    selection_seed = req.seed if req.seed is not None else secrets.randbelow(2_147_483_648)
    n = min(req.sample_size or limit, limit)
    try:
        seeds = await run_in_threadpool(
            _preview_rows,
            list(version.splits),
            list(req.seed_splits),
            held_out,
            req.prompt_column,
            needed,
            n,
            selection_seed,
        )
    except rules.GenerationRuleError as exc:
        raise _rule_error(exc) from None
    if not seeds:
        raise UnprocessableError(
            "The seed splits hold no source rows to preview from.",
            code="NO_SEED_ROWS",
            details={"seed_splits": list(req.seed_splits)},
        )
    return seeds, selection_seed


async def preview(db: AsyncSession, req: PreviewRequest) -> PreviewOut:
    """≤ 5 prompts or seed rows, synchronous, ≤ 60 s, no lease, refuse-load, the same steering
    check; writes no row, no file and no job (FR-007.44)."""
    settings = get_settings()
    limit = settings.generation_preview_max_prompts
    if req.prompts is not None and len(req.prompts) > limit:
        raise UnprocessableError(
            f"A preview generates at most {limit} prompts.",
            code="PREVIEW_TOO_LARGE",
        )
    template: GenerationTemplate | None = None
    if req.respond_template_id is not None:
        template = await template_service.get(db, req.respond_template_id)
        if template.kind != "respond":
            raise UnprocessableError(
                "A preview uses a respond template.", code="TEMPLATE_KIND_MISMATCH"
            )
    seeds, selection_seed = await _preview_seeds(db, req, template, limit)
    resolved, server = await _endpoint(db)
    supported = bool(settings.millm_steering_supported)
    side = await run_in_threadpool(
        resolve_side, "generator", req.setting.model_dump(), resolved, server, supported
    )
    deadline = settings.generation_preview_timeout_seconds
    try:
        items, stopped = await asyncio.wait_for(
            run_in_threadpool(
                _preview_sync,
                resolved,
                server,
                side,
                dict(template.body) if template is not None else None,
                seeds,
                req.seed,
                settings.generation_engine_path,
                deadline,
            ),
            timeout=deadline + 5,
        )
    except TimeoutError:
        raise AppError(
            "The preview took longer than 60 s; try fewer prompts.",
            code="PREVIEW_TIMEOUT",
            status_code=504,
        ) from None
    except CallStop as stop:
        raise AppError(stop.message, code=stop.code, status_code=409) from None
    return PreviewOut(
        items=items,
        snapshot=side.out(),
        server_kind=server.kind,
        model_id=resolved.model_id,
        stopped_early=stopped,
        selection_seed=selection_seed,
    )


# --- candidate build ------------------------------------------------------------------------

CANDIDATE_RECIPE_PREFIX = "generation-candidate-"


async def candidate_request(db: AsyncSession, run_id: str, who: Who, seed: int | None) -> Any:
    """The 002 build request for candidate version C = V + every generated row (FR-007.2, 007.12):
    a system recipe (one ``dw_generated_rows`` step) and the ``generation_run`` binding. The build
    never calls an endpoint; it reads the run's committed records."""
    from ...models.recipe import Recipe
    from ...schemas.versions import BindingRef, VersionBuildRequest, VersionInputRef
    from .. import recipe_service

    run = await get_run(db, run_id)
    if run.state != "completed":
        raise ConflictError(
            f"Generation run {run_id} is {run.state}; build a candidate from a completed run.",
            code="RUN_NOT_COMPLETED",
            details={"state": run.state},
        )
    version = await get_version_row(db, str(run.input_version_id))
    name = f"{CANDIDATE_RECIPE_PREFIX}{run.id}"
    recipe = (await db.execute(select(Recipe).where(Recipe.name == name))).scalar_one_or_none()
    if recipe is None:
        recipe = await recipe_service.create(
            db,
            who,
            name=name,
            description=f"System recipe: add generation run {run.id}'s rows (feature 007).",
            body={
                "format": "dw.recipe/v1",
                "steps": [
                    {
                        "operator": "dw_generated_rows",
                        "version": "1",
                        "params": {"generation_run_id": run.id, "target_type": run.target_type},
                    }
                ],
            },
            step_labels=["Add generated rows"],
        )
    assert recipe.head_revision_id is not None
    return VersionBuildRequest(
        dataset_id=str(version.dataset_id),
        inputs=[VersionInputRef(kind="version", version_id=str(version.id))],
        recipe_revision_id=str(recipe.head_revision_id),
        bindings=[BindingRef(kind="generation_run", id=run.id)],
        seed=seed,
    )
