"""What a generation run is for, when it ran and what it produced (operator request 2026-10-21).

The run row records ids; a person reading a run tile needs names. Everything here is READ from rows
that already exist, never inferred:

- timing: the run's ``created_at`` and ``completed_at`` and its jobs' ``started_at``/``completed_at``;
- the input version by dataset name and number;
- the expand and respond templates by name, version and description;
- the behaviour (015): the rubric of the minimal-pair chain whose generate stage is this run, and
  every live detector set with a role over a version of a dataset this run read or fed (its input
  version, a candidate version built from it, the chain's scope and pair versions). A record with
  no behaviour key set still says what a positive means, so it is listed with ``key=None``;
- the candidate versions built from the run (002 versions binding ``generation_run``);
- an approval whose result is this run, when one exists.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.approval import Approval
from ...models.dataset import Dataset
from ...models.detector_set import DetectorSet, DetectorSetRole
from ...models.generation import GenerationRun, GenerationTemplate
from ...models.job import Job
from ...models.minimal_pair_chain import MinimalPairChain
from ...models.rubric import Rubric
from ...models.version import Version
from ...schemas.generation import (
    ChainRef,
    NamedVersion,
    RunBehaviour,
    RunTiming,
    TemplateRef,
)


def _seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round(max(0.0, (end - start).total_seconds()), 3)


def run_timing(run: GenerationRun, jobs: list[Job]) -> RunTiming:
    """Queued → first job started → run finished. ``total_seconds`` runs from the first start to
    the finish (``None`` while the run is unfinished); ``active_seconds`` sums finished job spans.
    """
    starts = [j.started_at for j in jobs if j.started_at is not None]
    started = min(starts) if starts else None
    spans = [
        (j.completed_at - j.started_at).total_seconds()
        for j in jobs
        if j.started_at is not None and j.completed_at is not None
    ]
    return RunTiming(
        queued_at=run.created_at,
        started_at=started,
        finished_at=run.completed_at,
        queued_seconds=_seconds(run.created_at, started),
        total_seconds=_seconds(started, run.completed_at),
        active_seconds=round(float(sum(spans)), 3) if spans else None,
        attempts=len(jobs),
    )


async def _named_versions(db: AsyncSession, versions: list[Version]) -> list[NamedVersion]:
    if not versions:
        return []
    ids = {str(v.dataset_id) for v in versions}
    names = {
        str(d.id): d.name
        for d in (await db.execute(select(Dataset).where(Dataset.id.in_(ids)))).scalars()
    }
    return [
        NamedVersion(
            id=str(v.id),
            dataset_id=str(v.dataset_id),
            dataset_name=names.get(str(v.dataset_id), "unknown dataset"),
            number=int(v.number),
            created_at=v.created_at,
        )
        for v in versions
    ]


async def _version(db: AsyncSession, version_id: str | None) -> Version | None:
    return await db.get(Version, version_id) if version_id else None


async def _template(db: AsyncSession, template_id: str | None) -> TemplateRef | None:
    if not template_id:
        return None
    row = await db.get(GenerationTemplate, template_id)
    if row is None:
        return None
    sampling = (row.body or {}).get("sampling")
    return TemplateRef(
        id=row.id,
        name=row.name,
        version=int(row.version),
        kind=row.kind,
        description=row.description,
        sampling=sampling if isinstance(sampling, dict) else None,
    )


async def run_context(db: AsyncSession, run: GenerationRun, jobs: list[Job]) -> dict[str, Any]:
    """The context fields of ``GenerationRunOut`` for one run."""
    input_version = await _version(db, str(run.input_version_id))
    candidates = list(
        (
            await db.execute(
                select(Version)
                .where(Version.bindings.contains([{"kind": "generation_run", "id": run.id}]))
                .where(Version.deleted_at.is_(None))
                .order_by(Version.created_at)
            )
        ).scalars()
    )
    chain = (
        await db.execute(
            select(MinimalPairChain)
            .where(MinimalPairChain.generation_run_id == run.id)
            .order_by(MinimalPairChain.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    behaviours: list[RunBehaviour] = []
    chain_ref: ChainRef | None = None
    chain_versions: list[Version] = []
    if chain is not None:
        request = chain.request or {}
        rubric_id = request.get("rubric_id")
        rubric = await db.get(Rubric, rubric_id) if isinstance(rubric_id, str) else None
        pair = await _version(db, chain.pair_version_id)
        scope = await _version(db, chain.scope_version_id)
        chain_versions = [v for v in (scope, pair) if v is not None]
        named_pair = (await _named_versions(db, [pair]))[0] if pair is not None else None
        chain_ref = ChainRef(
            id=chain.id,
            state=chain.state,
            stage=chain.stage,
            rubric_id=rubric.id if rubric else (rubric_id if isinstance(rubric_id, str) else None),
            rubric_name=f"{rubric.name} v{rubric.version}" if rubric else None,
            flip_from=request.get("flip_from"),
            flip_to=request.get("flip_to"),
            pair_version=named_pair,
        )
        if rubric is not None:
            behaviours.append(
                RunBehaviour(
                    key=rubric.behaviour_key,
                    meaning=None,
                    source="rubric",
                    source_id=rubric.id,
                    source_name=f"{rubric.name} v{rubric.version}",
                )
            )

    fed = [v for v in (input_version, *candidates, *chain_versions) if v is not None]
    dataset_ids = {str(v.dataset_id) for v in fed}
    if dataset_ids:
        sets = (
            await db.execute(
                select(DetectorSet)
                .join(DetectorSetRole, DetectorSetRole.set_id == DetectorSet.id)
                .join(Version, Version.id == DetectorSetRole.version_id)
                .where(Version.dataset_id.in_(dataset_ids))
                .where(DetectorSet.archived.is_(False))
                .distinct()
                .order_by(DetectorSet.created_at.desc(), DetectorSet.id)
            )
        ).scalars()
        for row in sets:
            behaviours.append(
                RunBehaviour(
                    key=row.behaviour_key,
                    meaning=row.positive_meaning or None,
                    source="detector_set",
                    source_id=row.id,
                    source_name=row.name,
                )
            )
    # Most specific first: a recorded key beats none; a stated meaning beats a bare rubric name.
    behaviours.sort(key=lambda b: (b.key is None, b.meaning is None))
    # 017 OQ-017-4 (a): a key recorded on the run at start is authoritative; the derived records
    # are kept, and one naming ANOTHER key is reported, never silently preferred.
    note: str | None = None
    if run.behaviour_key:
        others = sorted({b.key for b in behaviours if b.key and b.key != run.behaviour_key})
        if others:
            note = (
                f"This run was started for behaviour {run.behaviour_key!r}; "
                + ", ".join(
                    f"{b.source.replace('_', ' ')} {b.source_name} now says {b.key!r}"
                    for b in behaviours
                    if b.key in others
                )
                + f". The run's recorded key {run.behaviour_key!r} stands."
            )
        behaviours.insert(
            0,
            RunBehaviour(
                key=run.behaviour_key,
                meaning=None,
                source="run",
                source_id=run.id,
                source_name="recorded at start",
            ),
        )

    approval = (
        await db.execute(
            select(Approval.id)
            .where(Approval.result_id.in_([run.id, *([chain.id] if chain else [])]))
            .order_by(Approval.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    named_input = await _named_versions(db, [input_version] if input_version else [])
    return {
        "timing": run_timing(run, jobs),
        "input_version": named_input[0] if named_input else None,
        "expand_template": await _template(db, run.expand_template_id),
        "respond_template": await _template(db, run.respond_template_id),
        "behaviour": behaviours[0] if behaviours else None,
        "behaviours": behaviours,
        "behaviour_key": run.behaviour_key,
        "behaviour_note": note,
        "minimal_pair_chain": chain_ref,
        "candidate_versions": await _named_versions(db, candidates),
        "approval_id": approval,
    }
