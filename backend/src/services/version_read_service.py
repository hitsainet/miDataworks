"""Version reads: detail and lists with head and supersession computed (FR-002.4; FTDD 002 §4.7).

Head and supersession are never stored: the head is the highest-numbered completed version of a
dataset, computed here with a window function, so a deleted head is never shown as current and a
new version supersedes the old one without touching it (FR-002.3).

``superseded_by`` follows lineage (FU-7a gap (k), 2026-11-27): a later completed version of the
same dataset supersedes a version when it was built from it (directly or through other versions)
or rebuilds it (the same recipe over the same inputs, or over inputs that supersede them). Before,
every non-head version read ``superseded_by: <head>``, so the reserve split RS v32 read "superseded
by 33" when FR v33 was a SIBLING built from the same input with another recipe. ``is_head`` is
unchanged: the dataset's highest-numbered completed version; ``head_number`` names it.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import NotFoundError
from ..models.dataset import Dataset
from ..models.enums import VersionState
from ..models.recipe import RecipeRevision
from ..models.step_execution import StepExecution
from ..models.version import Version, VersionStep
from ..schemas.versions import (
    SplitOut,
    VersionOut,
    VersionSummary,
    WspPartitionOut,
    WspPartitionsOut,
)


@dataclass(frozen=True)
class Supersession:
    is_head: bool
    superseded_by: int | None
    head_number: int | None = None


async def head_numbers(db: AsyncSession, dataset_ids: list[str]) -> dict[str, int]:
    """dataset id -> its head version number (completed versions only)."""
    if not dataset_ids:
        return {}
    rows = await db.execute(
        select(Version.dataset_id, func.max(Version.number))
        .where(Version.dataset_id.in_(dataset_ids), Version.state == VersionState.COMPLETED)
        .group_by(Version.dataset_id)
    )
    return {str(d): int(n) for d, n in rows.all()}


@dataclass(frozen=True)
class _Node:
    id: str
    number: int
    completed: bool
    recipe_id: str | None
    inputs: tuple[str, ...]  # canonical input refs, in request order
    parents: tuple[str, ...]  # version inputs of the same dataset


def _ref(item: Any) -> str:
    if isinstance(item, dict) and item.get("kind") == "version":
        return f"version:{item.get('version_id')}"
    return json.dumps(item, sort_keys=True, default=str)


def successors(nodes: list[_Node]) -> dict[str, int | None]:
    """version id -> the number of the newest later COMPLETED version that supersedes it (FU-7a
    gap (k)): one built from it, directly or through other versions of the dataset, or one that
    rebuilds it (the same recipe, its inputs position by position the same or superseding)."""
    by_id = {n.id: n for n in nodes}
    ancestors: dict[str, frozenset[str]] = {}

    def ancestors_of(node_id: str, seen: frozenset[str] = frozenset()) -> frozenset[str]:
        if node_id in ancestors:
            return ancestors[node_id]
        found: set[str] = set()
        for parent in by_id[node_id].parents:
            if parent in seen:
                continue  # never in practice (a version reads only older ones); never loops
            found |= {parent, *ancestors_of(parent, seen | {node_id})}
        ancestors[node_id] = frozenset(found)
        return ancestors[node_id]

    memo: dict[tuple[str, str], bool] = {}

    def supersedes(later: _Node, earlier: _Node) -> bool:
        key = (later.id, earlier.id)
        if key not in memo:
            memo[key] = later.number > earlier.number and (
                earlier.id in ancestors_of(later.id) or rebuilds(later, earlier)
            )
        return memo[key]

    def same_or_newer(a: str, b: str) -> bool:
        if a == b:
            return True
        x, y = by_id.get(a.removeprefix("version:")), by_id.get(b.removeprefix("version:"))
        return x is not None and y is not None and supersedes(x, y)

    def rebuilds(later: _Node, earlier: _Node) -> bool:
        return (
            later.recipe_id is not None
            and later.recipe_id == earlier.recipe_id
            and len(later.inputs) == len(earlier.inputs)
            and all(same_or_newer(a, b) for a, b in zip(later.inputs, earlier.inputs, strict=True))
        )

    ordered = sorted(nodes, key=lambda n: n.number)
    out: dict[str, int | None] = {}
    for i, node in enumerate(ordered):
        newer = [w.number for w in ordered[i + 1 :] if w.completed and supersedes(w, node)]
        out[node.id] = max(newer) if newer else None
    return out


async def superseded_numbers(db: AsyncSession, dataset_ids: list[str]) -> dict[str, int | None]:
    """version id -> ``superseded_by`` for every version of the datasets (:func:`successors`)."""
    if not dataset_ids:
        return {}
    rows = (
        await db.execute(
            select(
                Version.id,
                Version.dataset_id,
                Version.number,
                Version.state,
                Version.inputs,
                RecipeRevision.recipe_id,
            )
            .outerjoin(RecipeRevision, RecipeRevision.id == Version.recipe_revision_id)
            .where(Version.dataset_id.in_(dataset_ids))
        )
    ).all()
    datasets: dict[str, list[tuple[Any, ...]]] = {}
    for row in rows:
        datasets.setdefault(str(row[1]), []).append(tuple(row))
    out: dict[str, int | None] = {}
    for members in datasets.values():
        ids = {str(r[0]) for r in members}
        nodes = []
        for vid, _, number, state, inputs, recipe_id in members:
            items = list(inputs or [])
            nodes.append(
                _Node(
                    id=str(vid),
                    number=int(number),
                    completed=state == VersionState.COMPLETED,
                    recipe_id=str(recipe_id) if recipe_id is not None else None,
                    inputs=tuple(_ref(i) for i in items),
                    parents=tuple(
                        str(i["version_id"])
                        for i in items
                        if isinstance(i, dict)
                        and i.get("kind") == "version"
                        and str(i.get("version_id")) in ids
                    ),
                )
            )
        out.update(successors(nodes))
    return out


def supersession(
    version: Version, head: int | None, superseded_by: int | None = None
) -> Supersession:
    """``is_head`` from the dataset's head; ``superseded_by`` from lineage (:func:`successors`)."""
    is_head = (
        version.state == VersionState.COMPLETED and head is not None and version.number == head
    )
    return Supersession(is_head, None if is_head else superseded_by, head)


def _uuid(value: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise NotFoundError(
            f"{value!r} is not a valid version id.", code="version_not_found"
        ) from None


async def get_version_row(db: AsyncSession, version_id: str) -> Version:
    row = await db.get(Version, _uuid(version_id), populate_existing=True)
    if row is None:
        raise NotFoundError(f"No version {version_id}.", code="version_not_found")
    return row


async def step_reports(db: AsyncSession, version_id: str) -> list[dict[str, Any]]:
    """Each operator step's stored reports, in step order (only steps that returned one)."""
    rows = (
        await db.execute(
            select(VersionStep.step_index, VersionStep.reused, StepExecution)
            .join(StepExecution, StepExecution.id == VersionStep.step_execution_id)
            .where(VersionStep.version_id == version_id)
            .order_by(VersionStep.step_index)
        )
    ).all()
    return [
        {
            "step_index": int(index),
            "operator": exe.operator_name,
            "operator_version": exe.operator_version,
            "reused": bool(reused),
            "reports": list(exe.reports),
        }
        for index, reused, exe in rows
        if exe.reports
    ]


async def empty_splits(db: AsyncSession, v: Version) -> list[dict[str, Any]]:
    """FU-7a gap (j): the splits the build's last split-declaring step named that received no row.

    Finalize writes a file per split that holds rows (and a declared-empty held-out split of a
    plan); a named split that received none was neither written nor listed, so RS v32 read as one
    split while its split step had named four. Read from that step's stored ``split_roles``, the
    roles finalize used, so existing versions answer too."""
    rows = (
        await db.execute(
            select(VersionStep.step_index, StepExecution.split_roles)
            .join(StepExecution, StepExecution.id == VersionStep.step_execution_id)
            .where(VersionStep.version_id == v.id)
            .order_by(VersionStep.step_index)
        )
    ).all()
    declared = [(int(index), roles) for index, roles in rows if roles]
    if not declared:
        return []
    index, roles = declared[-1]
    written = {str(s["name"]) for s in v.splits}
    return [
        {
            "name": str(name),
            "held_out": bool((role or {}).get("held_out")),
            "rows": 0,
            "declared_by_step": index,
            "written": False,
        }
        for name, role in sorted(roles.items())
        if str(name) not in written
    ]


def split_out(split: dict[str, Any]) -> SplitOut:
    """A stored split entry, its proof marked when it compared against nothing (FU-7a gap (j));
    a proof stored before the mark is read the same way: no training value at all."""
    proof = split.get("proof")
    if isinstance(proof, dict) and "compared_against_nothing" not in proof:
        from .curation.held_out_proof import VACUOUS_NOTE

        vacuous = not any((proof.get("training_values") or {}).values())
        proof = {
            **proof,
            "compared_against_nothing": vacuous,
            **({"note": VACUOUS_NOTE} if vacuous else {}),
        }
        split = {**split, "proof": proof}
    return SplitOut(**split)


async def _rule_sets(db: AsyncSession, v: Version) -> dict[str, Any]:
    """016 FR-016.26: the rule sets the build ran, or "not recorded"."""
    from .rules import carry  # imported here: models import this module (delete checkers)

    return carry.read_block(await carry.carried_async(db, v))


async def _code_rules(db: AsyncSession, v: Version) -> dict[str, Any]:
    """018 FR-018.42: the code rules the build ran, each with its approval state now."""
    from .code_rules import carry

    return await carry.read_block(db, v)


async def version_out(db: AsyncSession, version_id: str) -> VersionOut:
    v = await get_version_row(db, version_id)
    dataset = await db.get(Dataset, v.dataset_id)
    assert dataset is not None
    heads = await head_numbers(db, [v.dataset_id])
    later = await superseded_numbers(db, [v.dataset_id])
    s = supersession(v, heads.get(v.dataset_id), later.get(str(v.id)))
    revision = await db.get(RecipeRevision, v.recipe_revision_id)
    return VersionOut(
        id=v.id,
        dataset_id=v.dataset_id,
        dataset_name=dataset.name,
        target_type=dataset.target_type,
        number=v.number,
        state=v.state,
        is_head=s.is_head,
        superseded_by=s.superseded_by,
        head_number=s.head_number,
        parent_version_id=v.parent_version_id,
        request_digest=v.request_digest,
        inputs=list(v.inputs),
        recipe_hash=v.recipe_hash,
        recipe_revision_id=v.recipe_revision_id,
        recipe_id=revision.recipe_id if revision else None,
        seed=int(v.seed),
        bindings=list(v.bindings),
        rowkey_scheme=v.rowkey_scheme,
        column_roles=dict(v.column_roles),
        splits=[split_out(split) for split in v.splits],
        empty_splits=await empty_splits(db, v),
        total_rows=int(v.total_rows),
        total_bytes=int(v.total_bytes),
        held_out_origin_version_id=v.held_out_origin_version_id,
        warnings=list(v.warnings),
        drop_summary=list(v.drop_summary),
        step_reports=await step_reports(db, v.id),
        manifest_sha256=v.manifest_sha256,
        build_job_id=v.build_job_id,
        created_by=v.created_by,
        created_by_origin=v.created_by_origin,
        created_at=v.created_at,
        deleted_by=v.deleted_by,
        deleted_by_origin=v.deleted_by_origin,
        deleted_at=v.deleted_at,
        delete_reason=v.delete_reason,
        rule_sets=await _rule_sets(db, v),
        code_rules=await _code_rules(db, v),
        behaviour_definition=v.behaviour_definition,
        behaviour_configuration=v.behaviour_configuration,
        behaviour_census=v.behaviour_census,
        **await _sensitivity_fields(db, v),
    )


async def _sensitivity_fields(db: AsyncSession, v: Version) -> dict[str, Any]:
    """021 FR-021.20, FR-021.24: the live value through the one helper, never the column."""
    from . import sensitivity

    def read(session: Any) -> dict[str, Any]:
        value, basis = sensitivity.effective(session, v)
        return {
            "sensitivity": value,
            "sensitivity_basis": basis,
            # R3 review fix (OR3-021-5), 2026-11-16: one lineage walk per read
            "sensitivity_published_below": sensitivity.published_below(session, v, now=value),
        }

    result: dict[str, Any] = await db.run_sync(read)
    return result


async def wsp_partitions_out(
    db: AsyncSession, version_id: str, role: str | None = None
) -> WspPartitionsOut:
    """021 FR-021.26: from the stored split entries (``version_length`` measured at build time);
    opens no file in the API process (R1A-D12)."""
    v = await get_version_row(db, version_id)
    fields = await _sensitivity_fields(db, v)
    partitions = [
        WspPartitionOut(
            split=s["name"],
            role=s["wsp"]["role"],
            set_kind=s["wsp"]["set_kind"],
            rows=int(s["rows"]),
            text_column=s["wsp"]["text_column"],
            min_chars=int(s["wsp"]["min_chars"]),
            version_length=s["wsp"]["version_length"],
            file_sha256=s["file_sha256"],
            logical_digest=s["logical_digest"],
        )
        for s in v.splits
        if s.get("wsp") and (role is None or s["wsp"]["role"] == role)
    ]
    return WspPartitionsOut(
        version_id=v.id,
        sensitivity=fields["sensitivity"],
        sensitivity_basis=fields["sensitivity_basis"],
        partitions=partitions,
    )


def summary(
    v: Version, dataset: Dataset, head: int | None, superseded_by: int | None = None
) -> VersionSummary:
    s = supersession(v, head, superseded_by)
    return VersionSummary(
        id=v.id,
        dataset_id=v.dataset_id,
        dataset_name=dataset.name,
        target_type=dataset.target_type,
        number=v.number,
        state=v.state,
        is_head=s.is_head,
        superseded_by=s.superseded_by,
        head_number=s.head_number,
        parent_version_id=v.parent_version_id,
        total_rows=int(v.total_rows),
        total_bytes=int(v.total_bytes),
        warnings_count=len(v.warnings),
        recipe_hash=v.recipe_hash,
        seed=int(v.seed),
        created_by=v.created_by,
        created_at=v.created_at,
    )


async def list_versions(
    db: AsyncSession,
    *,
    dataset_id: str | None = None,
    state: str | None = None,
    page: int = 1,
    limit: int = 50,
    order: str = "name",
    hide_archived_datasets: bool = False,
    wsp_role: str | None = None,
) -> tuple[list[VersionSummary], int]:
    """``hide_archived_datasets``: leave out the versions of an archived dataset (the default
    list and the UI's pickers; archive, 2026-10-20). Each version still reads by its ID."""
    query: Any = select(Version, Dataset).join(Dataset, Dataset.id == Version.dataset_id)
    count: Any = (
        select(func.count()).select_from(Version).join(Dataset, Dataset.id == Version.dataset_id)
    )
    if hide_archived_datasets:
        query = query.where(Dataset.archived_at.is_(None))
        count = count.where(Dataset.archived_at.is_(None))
    if dataset_id:
        try:
            did = str(uuid.UUID(dataset_id))
        except ValueError:
            return [], 0
        query = query.where(Version.dataset_id == did)
        count = count.where(Version.dataset_id == did)
    if state:
        query = query.where(Version.state == state)
        count = count.where(Version.state == state)
    if wsp_role:
        # 021 FR-021.25: versions with at least one split of the role (JSONB containment)
        has_role = Version.splits.contains([{"wsp": {"role": wsp_role}}])
        query = query.where(has_role)
        count = count.where(has_role)
    total = int((await db.execute(count)).scalar_one())
    rows = (
        await db.execute(
            query.order_by(
                *(
                    (Version.created_at.desc(), Version.number.desc())
                    if order == "recent"
                    else (Dataset.name, Version.number.desc())
                )
            )
            .offset((page - 1) * limit)
            .limit(limit)
        )
    ).all()
    page_datasets = sorted({str(v.dataset_id) for v, _ in rows})
    heads = await head_numbers(db, page_datasets)
    later = await superseded_numbers(db, page_datasets)
    return [
        summary(v, d, heads.get(str(v.dataset_id)), later.get(str(v.id))) for v, d in rows
    ], total


async def sensitivity_of(db: AsyncSession, version_id: str) -> dict[str, Any]:
    """021 FR-021.32: ``{sensitivity, basis}`` of a version now, for a label run's preflight."""
    v = await get_version_row(db, version_id)
    fields = await _sensitivity_fields(db, v)
    return {"sensitivity": fields["sensitivity"], "basis": fields["sensitivity_basis"]}
