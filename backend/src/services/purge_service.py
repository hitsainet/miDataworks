"""Purge: the operator's permanent delete of an ARCHIVED item (operator decision 2026-10-20).

Archive (``archive_service``) hides an item and deletes nothing. Purge is the step after it: it
removes an archived dataset, label run, generation run, approval, rule set (feature 016) or code
rule (feature 018 slice 4), everything that belongs only to it, and its files on disk, and leaves one :class:`~src.models.purge.PurgeEvent` tombstone saying
what went, who purged it, why, and the counts and bytes.

Rules, enforced here and nowhere else (the routes only parse and refuse agents):

- **Operator only.** The routes refuse any request carrying ``X-Dataworks-Agent`` (``403
  agent_forbidden``); there is no MCP tool; the tombstone's CHECK refuses any other origin.
- **Archived only.** Anything else is refused ``409 not_archived``.
- **What goes with it** (:data:`CASCADE`): a dataset takes its versions (tombstoned ones too) and
  their files, builds, steps, inputs, verifications, comparisons, reports, length profiles and
  diversity reports; every label run and generation run over one of its versions, with their
  labels, chunks, records, pairs, steering snapshots and files; its guided drafts and warning
  levels; the rule-set derivations made from its versions (D40, 016); approvals that name only
  it; then, if nothing else uses them, the jobs, step executions
  (and their row events and output files) and recipes those rows used. A run takes its own rows and
  files; an approval is one row.
- **Refused if anything outside that set still uses it** (``409 purge_blocked``, each blocker
  named): a job still working on it, an unfinished run, a pending approval naming it, and ANY row
  of ANY table holding a foreign key into the set that is not itself in the set (a publish record,
  a detector set, a miStudio registration, a calibration set, a review queue, a version of another
  dataset built from it, ...). That last check reads the live foreign-key graph from PostgreSQL, so
  a table added later blocks a purge until someone decides it belongs in :data:`CASCADE`: it fails
  closed. References that are not foreign keys are checked by name: a kept label run deriving from
  or reusing a purged run, an agreement report naming one, a kept version whose recipe or bindings
  name one, and a saved rule set whose provenance (its ``rule_source``) names a derivation of a
  purged version (D40: the derivation would go and the rule set would name nothing).

The plan (``dry_run=True``) runs exactly the same transaction and rolls it back, so what the
confirmation dialog lists is what the purge removes. The database refuses deletes from the
append-only and immutable tables unless ``dw.purge`` is set (migration ``0032``); this module sets
it with ``SET LOCAL``, so it ends with the transaction. Files are removed after the commit; a path
that could not be removed is reported, never hidden.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.agent_origin import Who
from ..core.errors import AppError, NotFoundError, UnprocessableError
from ..core.ids import new_id
from ..core.storage import PathOutsideDataDir, data_dir, resolve_under_data_dir
from ..models.purge import PURGE_KINDS, PurgeEvent
from .archive_service import (
    GENERATION_RUN_UNFINISHED,
    LABEL_RUN_UNFINISHED,
    LIVE_JOB,
    dataset_jobs,
)

logger = logging.getLogger(__name__)

REASON_MAX = 500
UUIDS = ("datasets", "versions")

#: Every table a purge deletes from, with the predicate that selects its rows from the purge set's
#: id arrays. The order is the delete order: children before the rows they reference.
CASCADE: dict[str, str] = {
    "dw_agent_label_rows": (
        "version_id = ANY(CAST(:versions AS uuid[])) OR run_id = ANY(CAST(:runs AS text[]))"
    ),
    "dw_labels": "label_run_id = ANY(CAST(:label_runs AS text[]))",
    "dw_label_run_chunks": "label_run_id = ANY(CAST(:label_runs AS text[]))",
    "dw_label_run_jobs": "label_run_id = ANY(CAST(:label_runs AS text[]))",
    "dw_label_runs": "id = ANY(CAST(:label_runs AS text[]))",
    "dw_generation_chunks": "run_id = ANY(CAST(:generation_runs AS text[]))",
    "dw_generation_pairs": "run_id = ANY(CAST(:generation_runs AS text[]))",
    "dw_generation_records": "run_id = ANY(CAST(:generation_runs AS text[]))",
    "dw_steering_snapshots": "run_id = ANY(CAST(:generation_runs AS text[]))",
    "dw_generation_run_jobs": "run_id = ANY(CAST(:generation_runs AS text[]))",
    "dw_generation_runs": "id = ANY(CAST(:generation_runs AS text[]))",
    "dw_version_comparisons": (
        "version_a = ANY(CAST(:versions AS uuid[])) OR version_b = ANY(CAST(:versions AS uuid[]))"
    ),
    "dw_length_profiles": "version_id = ANY(CAST(:versions AS uuid[]))",
    "dw_text_separability": "version_id = ANY(CAST(:versions AS uuid[]))",
    "dw_diversity_reports": "version_id = ANY(CAST(:versions AS uuid[]))",
    "dw_version_reports": "version_id = ANY(CAST(:versions AS uuid[]))",
    "dw_version_verifications": "version_id = ANY(CAST(:versions AS uuid[]))",
    "dw_version_steps": "version_id = ANY(CAST(:versions AS uuid[]))",
    "dw_version_inputs": "version_id = ANY(CAST(:versions AS uuid[]))",
    "dw_version_builds": "job_id = ANY(CAST(:build_jobs AS text[]))",
    # D40 (operator 2026-10-23): a version's rule-set derivations go with it. The purge stays
    # blocked while a saved rule set's provenance (the rule_source of what it builds) names one
    # (:func:`_named_blockers`), so no rule set is left naming a deleted derivation.
    "dw_rule_derivations": "source_version_id = ANY(CAST(:versions AS uuid[]))",
    "dw_versions": "id = ANY(CAST(:versions AS uuid[]))",
    "dw_recipe_drafts": "dataset_id = ANY(CAST(:datasets AS uuid[]))",
    "dw_shortcut_levels": "dataset_id = ANY(CAST(:datasets AS uuid[]))",
    "dw_approvals": "id = ANY(CAST(:approvals AS text[]))",
    "dw_datasets": "id = ANY(CAST(:datasets AS uuid[]))",
    # Feature 016: an archived, UNUSED rule set - its versions, then its head. A version a build
    # used is held by dw_rule_set_uses (RESTRICT), which blocks and is named, never cascaded.
    "dw_config_versions": (
        "(kind = 'rule_set' AND id = ANY(CAST(:rule_set_versions AS text[]))) OR "
        "(kind = 'code_rule' AND id = ANY(CAST(:code_rule_versions AS text[])))"
    ),
    "dw_rule_sets": "name = ANY(CAST(:rule_sets AS text[]))",
    # Feature 018 slice 4: an archived, UNUSED code rule - its versions (above), then its head.
    # A version a build ran is held by dw_code_rule_uses (RESTRICT): it blocks and is named. The
    # hash's approval list and contract runs are the operator's record and stay (keyed by hash).
    "dw_code_rules": "name = ANY(CAST(:code_rules AS text[]))",
}

#: What the operator calls a row of a table that keeps an item (the blocker's noun). A table
#: missing here still blocks; it is named by its table name.
NOUNS: dict[str, str] = {
    "dw_publishes": "publish record",
    "dw_publish_builds": "publish build",
    "dw_publish_check_runs": "publish check",
    "dw_exports": "export",
    "dw_detector_set_roles": "detector set",
    "dw_detector_sends": "detector-set send",
    "dw_mistudio_registrations": "miStudio registration",
    "dw_reproduction_links": "probe reproduction link",
    "dw_calibration_sets": "calibration set",
    "dw_calibration_records": "calibration record",
    "dw_calibration_targets": "calibration target",
    "dw_review_queues": "review queue",
    "dw_review_decisions": "review decision",
    "dw_agreement_reports": "agreement report",
    "dw_audits": "audit",
    "dw_minimal_pair_chains": "minimal-pair chain",
    "dw_source_annotations": "source annotation",
    "dw_versions": "version",
    "dw_version_inputs": "version built from it",
    "dw_version_builds": "version build",
    "dw_label_runs": "label run",
    "dw_generation_runs": "generation run",
    "dw_diversity_reports": "diversity report",
    "dw_version_comparisons": "version comparison",
    "dw_rule_set_uses": "build step that ran",
    "dw_rule_derivations": "rule derivation",
    "dw_config_versions": "configuration version",
    # Feature 018: an approval an operator's code-rule decision cites (FR-018.29) is kept.
    "dw_code_rule_approvals": "code-rule approval decision",
    "dw_code_rule_uses": "build step that ran",
}

#: The friendly name of each counted table (``counts`` keys are these).
COUNT_KEYS: dict[str, str] = {
    "dw_datasets": "datasets",
    "dw_versions": "versions",
    "dw_version_builds": "builds",
    "dw_version_steps": "version_steps",
    "dw_version_inputs": "version_inputs",
    "dw_version_verifications": "verifications",
    "dw_version_comparisons": "comparisons",
    "dw_version_reports": "reports",
    "dw_length_profiles": "length_profiles",
    "dw_text_separability": "wording_checks",
    "dw_diversity_reports": "diversity_reports",
    "dw_label_runs": "label_runs",
    "dw_labels": "labels",
    "dw_label_run_chunks": "label_chunks",
    "dw_label_run_jobs": "label_run_jobs",
    "dw_generation_runs": "generation_runs",
    "dw_generation_records": "generation_records",
    "dw_generation_pairs": "generation_pairs",
    "dw_generation_chunks": "generation_chunks",
    "dw_generation_run_jobs": "generation_run_jobs",
    "dw_steering_snapshots": "steering_snapshots",
    "dw_agent_label_rows": "agent_label_rows",
    "dw_recipe_drafts": "drafts",
    "dw_shortcut_levels": "warning_levels",
    "dw_approvals": "approvals",
    "dw_step_executions": "step_executions",
    "dw_row_events": "row_events",
    "dw_jobs": "jobs",
    "dw_recipes": "recipes",
    "dw_recipe_revisions": "recipe_revisions",
    "dw_recipe_bodies": "recipe_bodies",
    "dw_rule_set_uses": "rule_set_uses",
    "dw_rule_derivations": "rule_derivations",
    "dw_config_versions": "rule_set_versions",
    "dw_rule_sets": "rule_sets",
    "dw_code_rule_uses": "code_rule_uses",
    "dw_code_rules": "code_rules",
}


@dataclass(frozen=True)
class Blocker:
    code: str
    message: str
    table: str | None = None
    ids: tuple[str, ...] = ()

    def out(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "table": self.table, "ids": self.ids}


@dataclass
class Outcome:
    """One item's plan or purge."""

    kind: str
    id: str
    name: str | None = None
    status: str = "purgeable"  # purgeable | purged | blocked
    code: str | None = None
    message: str | None = None
    blockers: list[Blocker] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    bytes: int = 0
    paths: int = 0
    paths_left: list[str] = field(default_factory=list)
    tombstone_id: str | None = None
    #: Set on a batch item an earlier item of the same batch already removed with its cascade:
    #: ``{kind, id, name}`` of that item, whose tombstone (``tombstone_id``) records this one.
    removed_with: dict[str, str | None] | None = None
    #: ``(kind, id)`` of every OTHER purgeable item this purge removed with it. Not serialised.
    cascaded: list[tuple[str, str]] = field(default_factory=list)

    def out(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "code": self.code,
            "message": self.message,
            "blockers": [b.out() for b in self.blockers],
            "counts": self.counts,
            "bytes": self.bytes,
            "paths": self.paths,
            "paths_left": self.paths_left,
            "tombstone_id": self.tombstone_id,
            "removed_with": self.removed_with,
        }


@dataclass
class _Set:
    datasets: list[str] = field(default_factory=list)
    versions: list[str] = field(default_factory=list)
    label_runs: list[str] = field(default_factory=list)
    generation_runs: list[str] = field(default_factory=list)
    approvals: list[str] = field(default_factory=list)
    build_jobs: list[str] = field(default_factory=list)
    rule_sets: list[str] = field(default_factory=list)
    rule_set_versions: list[str] = field(default_factory=list)
    code_rules: list[str] = field(default_factory=list)
    code_rule_versions: list[str] = field(default_factory=list)

    def params(self) -> dict[str, list[str]]:
        return {
            "code_rules": self.code_rules,
            "code_rule_versions": self.code_rule_versions,
            "rule_sets": self.rule_sets,
            "rule_set_versions": self.rule_set_versions,
            "datasets": self.datasets,
            "versions": self.versions,
            "label_runs": self.label_runs,
            "generation_runs": self.generation_runs,
            "runs": self.label_runs + self.generation_runs,
            "approvals": self.approvals,
            "build_jobs": self.build_jobs,
        }

    def all_ids(self) -> list[str]:
        return self.datasets + self.versions + self.label_runs + self.generation_runs


class PurgeRefused(AppError):
    """``409``: carries the outcome so the batch can report it."""

    def __init__(self, outcome: Outcome) -> None:
        super().__init__(
            outcome.message or "Refused.",
            code=outcome.code or "purge_blocked",
            status_code=409,
            details={"blockers": [b.out() for b in outcome.blockers], "kind": outcome.kind},
        )
        self.outcome = outcome


# --- the foreign-key graph, read from PostgreSQL ------------------------------------------------


async def foreign_keys(db: AsyncSession) -> list[tuple[str, str, str, str]]:
    """``(child table, child column, parent table, parent column)`` for every single-column
    foreign key in the schema, partitions left out (their parent's key covers them)."""
    rows = await db.execute(text("""
            SELECT ch.relname, ca.attname, pa.relname, pc.attname
            FROM pg_constraint c
            JOIN pg_class ch ON ch.oid = c.conrelid
            JOIN pg_class pa ON pa.oid = c.confrelid
            JOIN pg_attribute ca ON ca.attrelid = c.conrelid AND ca.attnum = c.conkey[1]
            JOIN pg_attribute pc ON pc.attrelid = c.confrelid AND pc.attnum = c.confkey[1]
            JOIN pg_namespace n ON n.oid = ch.relnamespace
            WHERE c.contype = 'f' AND n.nspname = current_schema()
              AND NOT ch.relispartition AND cardinality(c.conkey) = 1
            ORDER BY 1, 2
            """))
    return [(r[0], r[1], r[2], r[3]) for r in rows]


def _unreferenced(fks: list[tuple[str, str, str, str]], table: str, alias: str = "t") -> str:
    """SQL: no row of any table holds a foreign key to this row of ``table``."""
    clauses = [
        # Names read from pg_catalog, never from a request.
        f"NOT EXISTS (SELECT 1 FROM {child} x WHERE x.{col} = {alias}.{pcol})"  # noqa: S608
        for child, col, parent, pcol in fks
        if parent == table
    ]
    return " AND ".join(clauses) or "TRUE"


# --- loading and the purge set -----------------------------------------------------------------


async def _lock(db: AsyncSession, kind: str, entity_id: str) -> dict[str, Any]:
    """The item's row, locked for this transaction, or ``404``."""
    table, key = {
        "dataset": ("dw_datasets", "id::text"),
        "label_run": ("dw_label_runs", "id"),
        "generation_run": ("dw_generation_runs", "id"),
        "approval": ("dw_approvals", "id"),
        "rule_set": ("dw_rule_sets", "name"),
        "code_rule": ("dw_code_rules", "name"),
    }[kind]
    row = (
        await db.execute(
            text(
                f"SELECT to_jsonb(t.*) || jsonb_build_object('id', t.{key}) FROM {table} t "  # noqa: S608
                f"WHERE {key} = :id FOR UPDATE"
            ),
            {"id": entity_id},
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(
            f"No {PURGE_KINDS[kind]} {entity_id}.", code="not_found", details={"kind": kind}
        )
    return dict(row)


async def _name(db: AsyncSession, kind: str, row: dict[str, Any]) -> str:
    if kind in ("dataset", "rule_set", "code_rule"):
        return str(row["name"])
    if kind == "approval":
        return f"{row['action']}: {row.get('summary') or row['target']}"
    found = (
        await db.execute(
            text(
                "SELECT d.name, v.number FROM dw_versions v JOIN dw_datasets d "
                "ON d.id = v.dataset_id WHERE v.id = CAST(:v AS uuid)"
            ),
            {"v": row["input_version_id"]},
        )
    ).first()
    over = f" over {found[0]} v{found[1]}" if found else ""
    noun = "label run" if kind == "label_run" else f"{row.get('mode', '')} generation run".strip()
    return f"{noun}{over}"


async def _ids(db: AsyncSession, sql: str, params: dict[str, Any]) -> list[str]:
    return sorted({str(r) for r in (await db.execute(text(sql), params)).scalars() if r})


async def _purge_set(db: AsyncSession, kind: str, entity_id: str) -> _Set:
    s = _Set()
    if kind == "dataset":
        s.datasets = [entity_id]
        s.versions = await _ids(
            db, "SELECT id FROM dw_versions WHERE dataset_id = CAST(:d AS uuid)", {"d": entity_id}
        )
        p = {"v": s.versions}
        s.label_runs = await _ids(
            db, "SELECT id FROM dw_label_runs WHERE input_version_id = ANY(CAST(:v AS uuid[]))", p
        )
        s.generation_runs = await _ids(
            db,
            "SELECT id FROM dw_generation_runs WHERE input_version_id = ANY(CAST(:v AS uuid[]))",
            p,
        )
        s.build_jobs = await _ids(
            db,
            "SELECT job_id FROM dw_version_builds WHERE dataset_id = CAST(:d AS uuid) "
            "OR version_id = ANY(CAST(:v AS uuid[])) OR verify_version_id = ANY(CAST(:v AS uuid[]))",
            {"d": entity_id, **p},
        )
    elif kind == "label_run":
        s.label_runs = [entity_id]
    elif kind == "generation_run":
        s.generation_runs = [entity_id]
    elif kind == "rule_set":
        s.rule_sets = [entity_id]
        s.rule_set_versions = await _ids(
            db,
            "SELECT id FROM dw_config_versions WHERE kind = 'rule_set' AND name = :n",
            {"n": entity_id},
        )
    elif kind == "code_rule":
        s.code_rules = [entity_id]
        s.code_rule_versions = await _ids(
            db,
            "SELECT id FROM dw_config_versions WHERE kind = 'code_rule' AND name = :n",
            {"n": entity_id},
        )
    else:
        s.approvals = [entity_id]
    return s


async def _approvals_naming(db: AsyncSession, s: _Set) -> tuple[list[str], list[str]]:
    """(non-pending approvals naming only the set and used by nothing outside it, pending ones)."""
    ids = s.all_ids()
    if not ids:
        return [], []
    rows = await db.execute(
        text("""
            SELECT a.id, a.status FROM dw_approvals a
            WHERE EXISTS (
              SELECT 1 FROM unnest(CAST(:ids AS text[])) i
              WHERE a.payload::text LIKE '%' || i || '%' OR a.target LIKE '%' || i || '%'
                 OR a.result_id = i
            )
            """),
        {"ids": ids},
    )
    found = [(str(r[0]), str(r[1])) for r in rows]
    return [a for a, st in found if st != "pending"], [a for a, st in found if st == "pending"]


# --- blockers -----------------------------------------------------------------------------------


async def _version_label(db: AsyncSession, version_id: str) -> str:
    found = (
        await db.execute(
            text(
                "SELECT d.name, v.number FROM dw_versions v JOIN dw_datasets d "
                "ON d.id = v.dataset_id WHERE v.id = CAST(:v AS uuid)"
            ),
            {"v": version_id},
        )
    ).first()
    return f"{found[0]} v{found[1]}" if found else version_id


def _row_ident(row: dict[str, Any]) -> str:
    for key in ("id", "name", "job_id", "version_id", "set_id"):
        if row.get(key) is not None:
            return str(row[key])
    return "?"


async def _fk_blockers(
    db: AsyncSession, s: _Set, fks: list[tuple[str, str, str, str]]
) -> list[Blocker]:
    """Rows OUTSIDE the purge set holding a foreign key to a row INSIDE it."""
    params = s.params()
    blockers: list[Blocker] = []
    for child, col, parent, pcol in fks:
        if parent not in CASCADE:
            continue
        keep = f"NOT ({CASCADE[child]})" if child in CASCADE else "TRUE"
        sql = (
            f"SELECT to_jsonb(c.*) FROM {child} c WHERE c.{col} IN "  # noqa: S608
            f"(SELECT p.{pcol} FROM {parent} p WHERE {CASCADE[parent]}) AND {keep} LIMIT 6"
        )
        rows = [dict(r) for r in (await db.execute(text(sql), params)).scalars()]
        if not rows:
            continue
        noun = NOUNS.get(child, child)
        if child in ("dw_rule_set_uses", "dw_code_rule_uses"):
            # A use names a step execution; the operator reads the version that linked it.
            found = await _ids(
                db,
                "SELECT vs.version_id FROM dw_version_steps vs "
                "WHERE vs.step_execution_id = ANY(CAST(:e AS uuid[]))",
                {"e": [str(r["step_execution_id"]) for r in rows]},
            )
            names = [await _version_label(db, v) for v in found[:5]] or [
                f"step execution {r['step_execution_id']}" for r in rows[:5]
            ]
            blockers.append(
                Blocker(
                    code="referenced",
                    message=f"version {', '.join(names)} was built with this "
                    + ("rule set" if child == "dw_rule_set_uses" else "code rule")
                    + " (a build step that ran it is recorded); a used version is never deleted.",
                    table=child,
                    ids=tuple(str(r["step_execution_id"]) for r in rows[:5]),
                )
            )
            continue
        if child == "dw_version_inputs" and any(r.get("kind") == "rule_source" for r in rows):
            # D40: a version whose rules a derivation from this one taught (its rule_source).
            taught = [r for r in rows if r.get("kind") == "rule_source"]
            names = [await _version_label(db, str(r["version_id"])) for r in taught[:5]]
            blockers.append(
                Blocker(
                    code="rule_source",
                    message=f"version {', '.join(names)} ran rules derived from this version "
                    "(its rule_source names it); a version that taught rules is kept while "
                    "anything built with them is.",
                    table=child,
                    ids=tuple(str(r["version_id"]) for r in taught[:5]),
                )
            )
            rows = [r for r in rows if r.get("kind") != "rule_source"]
            if not rows:
                continue
        if child in ("dw_versions", "dw_version_inputs"):
            key = "id" if child == "dw_versions" else "version_id"
            names = [await _version_label(db, str(r[key])) for r in rows[:5]]
            ids = tuple(str(r[key]) for r in rows[:5])
        else:
            names = [_row_ident(r) for r in rows[:5]]
            ids = tuple(names)
        more = " and more" if len(rows) > 5 else ""
        target = NOUNS.get(parent, parent.removeprefix("dw_").rstrip("s").replace("_", " "))
        blockers.append(
            Blocker(
                code="referenced",
                message=f"{noun} {', '.join(names)}{more} still uses this {target}.",
                table=child,
                ids=ids,
            )
        )
    return blockers


async def _named_blockers(db: AsyncSession, kind: str, s: _Set) -> list[Blocker]:
    """References that are not foreign keys, and work still in progress."""
    p = s.params()
    out: list[Blocker] = []
    if s.datasets:
        jobs = await dataset_jobs(db, s.datasets[0])
        if jobs:
            out.append(
                Blocker(
                    "job_running",
                    f"Job {jobs[0]} is working on it. Wait for it to finish or cancel it.",
                    "dw_jobs",
                    tuple(jobs),
                )
            )
    live = await _ids(
        db,
        f"""
        SELECT j.id FROM dw_jobs j WHERE j.status IN {LIVE_JOB!r} AND (
          j.id IN (SELECT job_id FROM dw_label_run_jobs
                   WHERE label_run_id = ANY(CAST(:label_runs AS text[])))
          OR j.id IN (SELECT job_id FROM dw_generation_run_jobs
                      WHERE run_id = ANY(CAST(:generation_runs AS text[])))
          OR j.params->>'label_run_id' = ANY(CAST(:label_runs AS text[]))
          OR j.params->>'generation_run_id' = ANY(CAST(:generation_runs AS text[])))
        """,  # noqa: S608 - a constant tuple
        p,
    )
    if live:
        out.append(
            Blocker(
                "job_running",
                f"Job {live[0]} is still running a run. Wait for it to finish or cancel it.",
                "dw_jobs",
                tuple(live),
            )
        )
    unfinished = await _ids(
        db,
        "SELECT id FROM dw_label_runs WHERE id = ANY(CAST(:label_runs AS text[])) "
        "AND state = ANY(CAST(:lstates AS text[])) UNION "
        "SELECT id FROM dw_generation_runs WHERE id = ANY(CAST(:generation_runs AS text[])) "
        "AND state = ANY(CAST(:gstates AS text[]))",
        {
            **p,
            "lstates": sorted(LABEL_RUN_UNFINISHED),
            "gstates": sorted(GENERATION_RUN_UNFINISHED),
        },
    )
    if unfinished:
        out.append(
            Blocker(
                "run_not_finished",
                f"Run {unfinished[0]} has not finished. Cancel it or let it finish.",
                "dw_label_runs" if unfinished[0].startswith("lr_") else "dw_generation_runs",
                tuple(unfinished),
            )
        )
    derived = await _ids(
        db,
        """
        SELECT r.id FROM dw_label_runs r
        WHERE NOT (r.id = ANY(CAST(:label_runs AS text[]))) AND (
          EXISTS (SELECT 1 FROM jsonb_array_elements_text(r.parent_run_ids) parent
                  WHERE parent = ANY(CAST(:label_runs AS text[])))
          OR EXISTS (SELECT 1 FROM dw_labels l WHERE l.label_run_id = r.id
                     AND l.reused_from_run_id = ANY(CAST(:label_runs AS text[]))))
        """,
        p,
    )
    if derived:
        out.append(
            Blocker(
                "derived_run",
                f"Label run {', '.join(derived[:5])} derives from or reuses labels of it.",
                "dw_label_runs",
                tuple(derived[:5]),
            )
        )
    agreement = await _ids(
        db,
        "SELECT id FROM dw_agreement_reports WHERE NOT (version_id = ANY(CAST(:versions AS "
        "uuid[]))) AND (probe_label_run_id = ANY(CAST(:label_runs AS text[])) OR "
        "judge_label_run_id = ANY(CAST(:label_runs AS text[])))",
        p,
    )
    if agreement:
        out.append(
            Blocker(
                "referenced",
                f"agreement report {', '.join(agreement[:5])} still uses this label run.",
                "dw_agreement_reports",
                tuple(agreement[:5]),
            )
        )
    runs = s.label_runs + s.generation_runs
    if runs:
        readers = await _ids(
            db,
            """
            SELECT v.id FROM dw_versions v JOIN dw_recipe_bodies b ON b.hash = v.recipe_hash
            WHERE NOT (v.id = ANY(CAST(:versions AS uuid[]))) AND v.state <> 'deleted'
              AND EXISTS (SELECT 1 FROM unnest(CAST(:runs AS text[])) i
                          WHERE b.body::text LIKE '%' || i || '%'
                             OR v.bindings::text LIKE '%' || i || '%')
            """,
            p,
        )
        if readers:
            names = [await _version_label(db, v) for v in readers[:5]]
            out.append(
                Blocker(
                    "read_by_version",
                    f"version {', '.join(names)} was built from it (its recipe or bindings name "
                    "the run).",
                    "dw_versions",
                    tuple(readers[:5]),
                )
            )
    if s.versions:
        # D40: the purge takes the derivations of its versions (CASCADE), but never while a saved
        # rule set's provenance names one of them (or names a purged version as its source):
        # that rule set's rule_source would point at nothing.
        naming = (
            await db.execute(
                text("""
                    SELECT c.name, c.number, c.body->'provenance'->>'derivation_id'
                    FROM dw_config_versions c
                    WHERE c.kind = 'rule_set' AND jsonb_typeof(c.body->'provenance') = 'object'
                      AND (c.body->'provenance'->>'derivation_id' IN (
                             SELECT d.id FROM dw_rule_derivations d
                             WHERE d.source_version_id = ANY(CAST(:versions AS uuid[])))
                           OR c.body->'provenance'->>'source_version_id'
                              = ANY(CAST(:versions AS text[])))
                    ORDER BY c.name, c.number
                    """),
                p,
            )
        ).all()
        if naming:
            sets = [f"{r[0]} v{r[1]}" for r in naming[:5]]
            more = " and more" if len(naming) > 5 else ""
            out.append(
                Blocker(
                    "rule_source",
                    f"rule set {', '.join(sets)}{more} was derived from this dataset (its "
                    f"provenance names derivation {naming[0][2]}); purge or replace the rule "
                    "set first, so no rule_source is left naming a deleted derivation.",
                    "dw_config_versions",
                    tuple(sorted({str(r[2]) for r in naming}))[:5],
                )
            )
    if kind == "rule_set":
        pinning = await _ids(
            db,
            """
            SELECT DISTINCT r.name FROM dw_recipe_bodies b
            JOIN dw_recipe_revisions rv ON rv.recipe_hash = b.hash
            JOIN dw_recipes r ON r.id = rv.recipe_id
            WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(b.body->'steps') st
                          WHERE st->>'operator' = 'rule_set'
                            AND st->'params'->>'rule_set_name' = ANY(CAST(:rule_sets AS text[])))
            """,
            p,
        )
        if pinning:
            out.append(
                Blocker(
                    "referenced",
                    f"recipe {', '.join(pinning[:5])} pins this rule set; move the recipe to "
                    "another rule set first.",
                    "dw_recipe_bodies",
                    tuple(pinning[:5]),
                )
            )
    if kind == "code_rule":
        # A saved rule set naming one of its hashes would name nothing after the purge.
        naming_sets = await _ids(
            db,
            """
            SELECT DISTINCT c.name || ' v' || c.number FROM dw_config_versions c
            WHERE c.kind = 'rule_set' AND EXISTS (
              SELECT 1 FROM jsonb_array_elements(c.body->'signals') sg
              WHERE sg->>'kind' = 'code_rule' AND sg->>'code_sha256' IN (
                SELECT v.body_sha256 FROM dw_config_versions v
                WHERE v.id = ANY(CAST(:code_rule_versions AS text[]))))
            """,
            p,
        )
        if naming_sets:
            out.append(
                Blocker(
                    "referenced",
                    f"rule set {', '.join(naming_sets[:5])} names this code rule; purge or "
                    "replace the rule set first.",
                    "dw_config_versions",
                    tuple(naming_sets[:5]),
                )
            )
    if kind == "approval":
        users = await _ids(
            db,
            "SELECT id FROM dw_label_runs WHERE approval_id = ANY(CAST(:approvals AS text[]))",
            p,
        )
        if users:
            out.append(
                Blocker(
                    "referenced",
                    f"label run {', '.join(users[:5])} records this approval.",
                    "dw_label_runs",
                    tuple(users[:5]),
                )
            )
    return out


# --- the purge ----------------------------------------------------------------------------------


async def _count_delete(db: AsyncSession, table: str, where: str, params: dict[str, Any]) -> int:
    result = await db.execute(text(f"DELETE FROM {table} WHERE {where}"), params)  # noqa: S608
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return total


def _paths(relative: set[str]) -> list[Path]:
    """Existing paths under the data volume, at least two segments deep, nested ones dropped."""
    base = data_dir().resolve()
    found: list[Path] = []
    for rel in relative:
        try:
            path = resolve_under_data_dir(rel)
        except PathOutsideDataDir:
            logger.warning("purge: %r is outside the data directory; left alone", rel)
            continue
        if len(path.relative_to(base).parts) < 2 or not path.exists():
            continue
        found.append(path)
    found.sort(key=lambda p: len(p.parts))
    kept: list[Path] = []
    for path in found:
        if not any(k == path or k in path.parents for k in kept):
            kept.append(path)
    return kept


async def _execute(db: AsyncSession, s: _Set, outcome: Outcome) -> list[Path]:
    """Delete everything; fill ``outcome.counts``; return the paths to remove."""
    fks = await foreign_keys(db)
    p: dict[str, Any] = s.params()
    counts: dict[str, int] = {}
    rel: set[str] = {f"versions/{v}" for v in s.versions}
    rel |= {f"runs/{r}" for r in s.label_runs + s.generation_runs}

    # What the cascade's rows used, collected before they go.
    jobs = await _ids(
        db,
        """
        SELECT job_id FROM dw_version_builds WHERE job_id = ANY(CAST(:build_jobs AS text[]))
        UNION SELECT build_job_id FROM dw_versions WHERE id = ANY(CAST(:versions AS uuid[]))
        UNION SELECT job_id FROM dw_label_run_jobs
              WHERE label_run_id = ANY(CAST(:label_runs AS text[]))
        UNION SELECT job_id FROM dw_label_run_chunks
              WHERE label_run_id = ANY(CAST(:label_runs AS text[]))
        UNION SELECT job_id FROM dw_generation_run_jobs
              WHERE run_id = ANY(CAST(:generation_runs AS text[]))
        UNION SELECT job_id FROM dw_generation_chunks
              WHERE run_id = ANY(CAST(:generation_runs AS text[]))
        UNION SELECT job_id FROM dw_version_reports WHERE version_id = ANY(CAST(:versions AS uuid[]))
        UNION SELECT job_id FROM dw_diversity_reports
              WHERE version_id = ANY(CAST(:versions AS uuid[]))
        UNION SELECT job_id FROM dw_version_verifications
              WHERE version_id = ANY(CAST(:versions AS uuid[]))
        UNION SELECT id FROM dw_jobs WHERE status NOT IN ('queued', 'running', 'cancelling') AND (
              params->>'version_id' = ANY(CAST(:versions AS text[]))
              OR params->>'label_run_id' = ANY(CAST(:label_runs AS text[]))
              OR params->>'generation_run_id' = ANY(CAST(:generation_runs AS text[]))
              OR params->>'dataset_id' = ANY(CAST(:datasets AS text[])))
        """,
        p,
    )
    executions = await _ids(
        db,
        "SELECT step_execution_id FROM dw_version_steps WHERE version_id = ANY(CAST(:versions AS "
        "uuid[])) UNION SELECT id FROM dw_step_executions WHERE job_id = ANY(CAST(:jobs AS text[]))",
        {**p, "jobs": jobs},
    )
    for row in (
        await db.execute(
            text(
                "SELECT id::text, artefacts FROM dw_version_reports "
                "WHERE version_id = ANY(CAST(:versions AS uuid[])) UNION ALL "
                "SELECT id::text, NULL FROM dw_diversity_reports "
                "WHERE version_id = ANY(CAST(:versions AS uuid[]))"
            ),
            p,
        )
    ).all():
        rel.add(f"runs/{row[0]}")
        for art in row[1] or []:
            parts = str(art.get("path", "")).split("/") if isinstance(art, dict) else []
            if len(parts) >= 2 and parts[0] == "runs":
                rel.add(f"runs/{parts[1]}")
    revisions = await _ids(
        db,
        "SELECT recipe_revision_id FROM dw_versions WHERE id = ANY(CAST(:versions AS uuid[])) "
        "UNION SELECT r.id FROM dw_recipe_revisions r JOIN dw_recipe_drafts d "
        "ON d.recipe_id = r.recipe_id WHERE d.dataset_id = ANY(CAST(:datasets AS uuid[]))",
        p,
    )
    recipes = await _ids(
        db,
        "SELECT recipe_id FROM dw_recipe_revisions WHERE id = ANY(CAST(:r AS uuid[])) UNION "
        "SELECT recipe_id FROM dw_recipe_drafts WHERE dataset_id = ANY(CAST(:datasets AS uuid[]))",
        {**p, "r": revisions},
    )
    hashes = await _ids(
        db,
        "SELECT recipe_hash FROM dw_versions WHERE id = ANY(CAST(:versions AS uuid[]))",
        p,
    )

    await db.execute(text("SET LOCAL dw.purge = 'on'"))
    # Feature 016: the use records of the step executions this purge removes go first, or they
    # would keep those executions (and their row events and files) alive. Executions a kept
    # version still links keep their uses.
    uses_gone = await _count_delete(
        db,
        "dw_rule_set_uses",
        "step_execution_id IN (SELECT t.id FROM dw_step_executions t WHERE "
        "t.id = ANY(CAST(:e AS uuid[])) AND NOT EXISTS (SELECT 1 FROM dw_version_steps vs "
        "WHERE vs.step_execution_id = t.id AND NOT (vs.version_id = ANY(CAST(:versions AS "
        "uuid[])))))",
        {**p, "e": executions},
    )
    if uses_gone:
        counts["rule_set_uses"] = uses_gone
    # Feature 018 slice 4: the same for the code rules those executions ran.
    code_uses_gone = await _count_delete(
        db,
        "dw_code_rule_uses",
        "step_execution_id IN (SELECT t.id FROM dw_step_executions t WHERE "
        "t.id = ANY(CAST(:e AS uuid[])) AND NOT EXISTS (SELECT 1 FROM dw_version_steps vs "
        "WHERE vs.step_execution_id = t.id AND NOT (vs.version_id = ANY(CAST(:versions AS "
        "uuid[])))))",
        {**p, "e": executions},
    )
    if code_uses_gone:
        counts["code_rule_uses"] = code_uses_gone
    for table, where in CASCADE.items():
        n = await _count_delete(db, table, where, p)
        if n:
            key = COUNT_KEYS[table]
            if table == "dw_config_versions" and s.code_rule_versions:
                key = "code_rule_versions"  # a code rule's purge removes no rule-set version
            counts[key] = counts.get(key, 0) + n

    # Step executions no kept version uses, their row events and output directories.
    gone = (
        await db.execute(
            text(
                "DELETE FROM dw_step_executions t WHERE t.id = ANY(CAST(:e AS uuid[])) AND "  # noqa: S608
                + _unreferenced(fks, "dw_step_executions")
                + " RETURNING t.id, t.output_dir"
            ),
            {"e": executions},
        )
    ).all()
    if gone:
        counts["step_executions"] = len(gone)
        rel |= {str(r[1]) for r in gone}
        n = await _count_delete(
            db,
            "dw_row_events",
            "step_execution_id = ANY(CAST(:e AS uuid[]))",
            {"e": [str(r[0]) for r in gone]},
        )
        if n:
            counts["row_events"] = n
    # Jobs nothing else records.
    done = (
        (
            await db.execute(
                text(
                    "DELETE FROM dw_jobs t WHERE t.id = ANY(CAST(:j AS text[])) AND "  # noqa: S608
                    "t.status NOT IN ('queued', 'running', 'cancelling') AND "
                    + _unreferenced(fks, "dw_jobs")
                    + " AND NOT EXISTS (SELECT 1 FROM dw_label_run_chunks c WHERE c.job_id = t.id)"
                    + " RETURNING t.id"
                ),
                {"j": jobs},
            )
        )
        .scalars()
        .all()
    )
    if done:
        counts["jobs"] = len(done)
        rel |= {f"runs/{j}" for j in done}
    # Recipes only this item used.
    if recipes:
        mine = await _ids(
            db,
            """
            SELECT r.id FROM dw_recipes r WHERE r.id = ANY(CAST(:recipes AS uuid[]))
              AND NOT EXISTS (SELECT 1 FROM dw_versions v JOIN dw_recipe_revisions rv
                              ON rv.id = v.recipe_revision_id WHERE rv.recipe_id = r.id)
              AND NOT EXISTS (SELECT 1 FROM dw_recipe_drafts d WHERE d.recipe_id = r.id)
              AND NOT EXISTS (SELECT 1 FROM dw_recipe_revisions other
                              JOIN dw_recipe_revisions rv ON rv.id = other.cloned_from_revision_id
                              WHERE rv.recipe_id = r.id AND other.recipe_id <> r.id)
              AND NOT EXISTS (SELECT 1 FROM dw_version_builds b
                              JOIN dw_recipe_revisions rv
                              ON rv.id::text = b.request->>'recipe_revision_id'
                              WHERE rv.recipe_id = r.id)
            """,
            {"recipes": recipes},
        )
        if mine:
            await db.execute(
                text(
                    "UPDATE dw_recipes SET head_revision_id = NULL "
                    "WHERE id = ANY(CAST(:m AS uuid[]))"
                ),
                {"m": mine},
            )
            freed = (
                (
                    await db.execute(
                        text(
                            "DELETE FROM dw_recipe_revisions WHERE recipe_id = ANY(CAST(:m AS uuid[])) "
                            "RETURNING recipe_hash"
                        ),
                        {"m": mine},
                    )
                )
                .scalars()
                .all()
            )
            counts["recipe_revisions"] = len(freed)
            hashes = sorted(set(hashes) | {str(h) for h in freed})
            counts["recipes"] = await _count_delete(
                db, "dw_recipes", "id = ANY(CAST(:m AS uuid[]))", {"m": mine}
            )
    if hashes:
        bodies = await db.execute(
            text(
                "DELETE FROM dw_recipe_bodies t WHERE t.hash = ANY(CAST(:h AS text[])) AND "  # noqa: S608
                + _unreferenced(fks, "dw_recipe_bodies")
                + " AND NOT EXISTS (SELECT 1 FROM dw_version_builds b"
                " WHERE b.request->>'recipe_hash' = t.hash)"
            ),
            {"h": hashes},
        )
        if bodies.rowcount:  # type: ignore[attr-defined]
            counts["recipe_bodies"] = int(bodies.rowcount)  # type: ignore[attr-defined]
    outcome.counts = dict(sorted(counts.items()))
    return _paths(rel)


async def _plan_or_purge(
    db: AsyncSession, kind: str, entity_id: str, who: Who | None, reason: str | None
) -> tuple[Outcome, list[Path]]:
    """Run the purge in the session's transaction. The caller commits or rolls back."""
    if kind not in PURGE_KINDS:
        raise UnprocessableError(f"{kind!r} cannot be purged.", code="not_purgeable")
    row = await _lock(db, kind, entity_id)
    entity_id = str(row["id"])
    outcome = Outcome(kind=kind, id=entity_id, name=await _name(db, kind, row))
    if row.get("archived_at") is None:
        outcome.status, outcome.code = "blocked", "not_archived"
        outcome.message = (
            f"{outcome.name} is not archived. Only an archived {PURGE_KINDS[kind]} can be deleted "
            "permanently; archive it first."
        )
        return outcome, []
    s = await _purge_set(db, kind, entity_id)
    approvals, pending = ([], []) if kind == "approval" else await _approvals_naming(db, s)
    if approvals:
        fks = await foreign_keys(db)
        s.approvals = await _ids(
            db,
            "SELECT t.id FROM dw_approvals t WHERE t.id = ANY(CAST(:a AS text[])) AND "  # noqa: S608
            + _unreferenced(fks, "dw_approvals")
            + " AND NOT EXISTS (SELECT 1 FROM dw_label_runs r WHERE r.approval_id = t.id"
            " AND NOT (r.id = ANY(CAST(:lr AS text[]))))",
            {"a": approvals, "lr": s.label_runs},
        )
    blockers = await _named_blockers(db, kind, s)
    blockers += await _fk_blockers(db, s, await foreign_keys(db))
    if pending:
        blockers.append(
            Blocker(
                "approval_pending",
                f"Approval {pending[0]} is pending and names it. Approve or reject it first.",
                "dw_approvals",
                tuple(pending),
            )
        )
    if blockers:
        outcome.status, outcome.code = "blocked", "purge_blocked"
        outcome.blockers = blockers
        extra = f" (+{len(blockers) - 1} more)" if len(blockers) > 1 else ""
        outcome.message = f"{outcome.name} cannot be deleted: {blockers[0].message}{extra}"
        return outcome, []
    paths = await _execute(db, s, outcome)
    outcome.cascaded = [
        (k, i)
        for k, ids in (
            ("dataset", s.datasets),
            ("label_run", s.label_runs),
            ("generation_run", s.generation_runs),
            ("approval", s.approvals),
        )
        for i in ids
        if (k, i) != (kind, entity_id)
    ]
    outcome.paths = len(paths)
    outcome.bytes = sum(_size(p) for p in paths)
    if who is not None:
        event = PurgeEvent(
            id=new_id("prg"),
            entity_kind=kind,
            entity_id=entity_id,
            name=outcome.name,
            reason=reason,
            purged_by=who.who,
            purged_by_origin=who.origin,
            counts=outcome.counts,
            bytes_removed=outcome.bytes,
            paths_removed=outcome.paths,
        )
        db.add(event)
        await db.flush()
        outcome.tombstone_id = event.id
    return outcome, paths


def _reason(reason: str | None) -> str | None:
    value = (reason or "").strip()
    return value[:REASON_MAX] or None


async def plan(db: AsyncSession, kind: str, entity_id: str) -> Outcome:
    """What a purge would remove, or why it is refused. Runs the purge and rolls it back."""
    try:
        outcome, _ = await _plan_or_purge(db, kind, entity_id, None, None)
    finally:
        await db.rollback()
    return outcome


async def purge(
    db: AsyncSession, who: Who, kind: str, entity_id: str, reason: str | None
) -> Outcome:
    """Purge one archived item; raises :class:`PurgeRefused` (``409``) when refused."""
    if who.origin != "operator":
        raise AppError(
            "Permanent delete is operator-only.", code="agent_forbidden", status_code=403
        )
    try:
        outcome, paths = await _plan_or_purge(db, kind, entity_id, who, _reason(reason))
    except BaseException:
        await db.rollback()
        raise
    if outcome.status == "blocked":
        await db.rollback()
        raise PurgeRefused(outcome)
    await db.commit()
    outcome.status = "purged"
    for path in paths:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
        if path.exists():
            outcome.paths_left.append(str(path.relative_to(data_dir().resolve())))
    if outcome.paths_left:
        logger.warning("purge %s %s left %s", kind, entity_id, outcome.paths_left)
    logger.info(
        "%s %s purged by %s: %s, %d bytes",
        PURGE_KINDS[kind],
        entity_id,
        who.who,
        outcome.counts,
        outcome.bytes,
    )
    return outcome


#: The order a batch purges in: runs, then approvals, then datasets. A dataset's purge removes
#: its runs and the approvals naming only it; purging those first, when they are in the batch,
#: gives each its own tombstone with its own counts instead of folding them into the dataset's.
BATCH_ORDER: dict[str, int] = {
    "label_run": 0,
    "generation_run": 0,
    "approval": 1,
    "dataset": 2,
    # Feature 016: after datasets, whose purge removes the use records that would block it.
    "rule_set": 3,
    # Feature 018: after rule sets, whose versions may name its hashes.
    "code_rule": 4,
}


def _removed_with(kind: str, entity_id: str, parent: Outcome) -> Outcome:
    """An item an earlier item of the same batch removed with it: purged, and its tombstone is
    the parent's."""
    noun = PURGE_KINDS.get(kind, kind)
    return Outcome(
        kind=kind,
        id=entity_id,
        status="purged",
        code="removed_with",
        message=(
            f"Deleted with {PURGE_KINDS[parent.kind]} {parent.name}, purged earlier in this "
            f"request: this {noun} belonged only to it, and that purge's record counts it."
        ),
        tombstone_id=parent.tombstone_id,
        removed_with={"kind": parent.kind, "id": parent.id, "name": parent.name},
    )


async def purge_many(
    db: AsyncSession, who: Who, items: list[tuple[str, str]], reason: str | None
) -> list[Outcome]:
    """Purge each item on its own; one refused item never stops the others. Results come back in
    the request's order.

    Items go in :data:`BATCH_ORDER` (runs, approvals, datasets), so an item that belongs to
    another item of the batch is purged on its own, with its own tombstone, before its parent.
    A blocked item is retried after the others, while a pass still purges something: an item
    blocked only by another item of the same request (a dataset built from another one, a run
    another run derives from) goes once its blocker has. An item that an earlier item's purge
    already removed with its cascade (a run still blocked when its dataset went) reports
    ``purged`` with ``removed_with`` naming that item, never ``not_found``: it is gone, and the
    parent's tombstone records it. An id no item of the batch removed stays ``not_found``.
    """
    results: dict[int, Outcome] = {}
    removed_by: dict[tuple[str, str], Outcome] = {}
    waiting = sorted(
        range(len(items)), key=lambda i: BATCH_ORDER.get(items[i][0], len(BATCH_ORDER))
    )
    while waiting:
        progress = False
        still: list[int] = []
        for index in waiting:
            kind, entity_id = items[index]
            try:
                outcome = await purge(db, who, kind, entity_id, reason)
                results[index] = outcome
                for key in outcome.cascaded:
                    removed_by.setdefault(key, outcome)
                progress = True
            except PurgeRefused as refused:
                results[index] = refused.outcome
                if refused.outcome.code == "purge_blocked":
                    still.append(index)
            except AppError as error:
                # Only a missing row can be one an earlier item took (``not_found``).
                parent = removed_by.get((kind, entity_id))
                results[index] = (
                    _removed_with(kind, entity_id, parent)
                    if parent is not None
                    else Outcome(
                        kind=kind,
                        id=entity_id,
                        status="blocked",
                        code=error.code,
                        message=error.message,
                    )
                )
        waiting = still if progress else []
    return [results[i] for i in range(len(items))]


async def plan_many(db: AsyncSession, items: list[tuple[str, str]]) -> list[Outcome]:
    out: list[Outcome] = []
    for kind, entity_id in items:
        try:
            out.append(await plan(db, kind, entity_id))
        except AppError as error:
            out.append(
                Outcome(
                    kind=kind,
                    id=entity_id,
                    status="blocked",
                    code=error.code,
                    message=error.message,
                )
            )
    return out


async def events(db: AsyncSession, kind: str, entity_id: str) -> list[PurgeEvent]:
    from sqlalchemy import select

    rows = await db.execute(
        select(PurgeEvent)
        .where(PurgeEvent.entity_kind == kind, PurgeEvent.entity_id == entity_id)
        .order_by(PurgeEvent.created_at)
    )
    return list(rows.scalars())


__all__ = [
    "BATCH_ORDER",
    "CASCADE",
    "NOUNS",
    "Blocker",
    "Outcome",
    "PurgeRefused",
    "foreign_keys",
    "plan",
    "plan_many",
    "purge",
    "purge_many",
]
