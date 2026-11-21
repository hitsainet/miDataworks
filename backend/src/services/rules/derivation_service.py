"""Rule derivations: start, read, run (FR-016.29 - FR-016.32; FTID 016 section 3.9).

**Start** validates BEFORE queueing: the source version exists and is completed; the text, label and
grouping columns exist (``label_column_missing`` names the missing one); the label column holds
exactly two classes and the declared positive values are among them (``label_column_not_binary``,
read with one DuckDB ``SELECT DISTINCT`` over the version's split files); ``max_signals`` is within
``RULE_DERIVATION_MAX_SIGNALS``. The row keeps the two settings in force (minimum held-out rows per
class, maximum source rows) in ``params``, so a later setting change never changes a stored result.

**Run** (the ``rule_derivation`` job, ``curation`` queue) reads the projected columns with DuckDB,
sampling above ``max_source_rows`` with ``ORDER BY hash(_dw_row_key, seed)``, runs
``rules/derivation.sequential_cover`` (cancellable at each literal), and stores the state, figures
and the draft (its RFC 8785 text and sha256). Nothing is saved as a rule set (FR-016.31): the agent
saves the draft with ``dataworks_save_rule_set``, whose provenance check reads this row. Logs carry
figures and counts, never row text.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from ...core.agent_origin import Who
from ...core.cancellation import CancelCheck, record_progress
from ...core.clock import utc_now
from ...core.config import get_settings
from ...core.ids import new_id
from ...models.enums import VersionState
from ...models.job import TERMINAL_STATUSES, Job
from ...models.rule_set import RuleDerivation
from ...models.version import Version
from ...rules import derivation, rule_set
from ...rules.next_step import next_for
from ..duck import connect, files_param
from ..identity import digest_text
from ..label_inputs import version_files
from ..publishing.projection import label_text
from .errors import RuleSetError

logger = logging.getLogger(__name__)

KIND = "rule_derivation"
TERMINAL = ("completed", "failed", "cancelled")
ROW_KEY = "_dw_row_key"


def _refuse(status: int, code: str, message: str, **details: Any) -> RuleSetError:
    return RuleSetError(
        status,
        code,
        message,
        details,
        next_tool="dataworks_get_version",
        next_args={"version_id": details.get("version_id")} if details.get("version_id") else {},
        why="read the version's columns and splits",
    )


def _distinct(version: Version, column: str) -> list[Any]:
    files = version_files(version.splits)
    con = connect()
    try:
        rows = con.execute(
            f'SELECT DISTINCT "{column}" FROM read_parquet(?) WHERE "{column}" IS NOT NULL '  # noqa: S608
            "LIMIT 3",
            [files_param(files)],
        ).fetchall()
    finally:
        con.close()
    return [r[0] for r in rows]


def _same(a: Any, b: Any) -> bool:
    """One label value as the other, keyed the way :func:`read_rows` compares them: booleans in
    lower case, like JSON and like DuckDB's ``CAST(... AS VARCHAR)`` (Review R1 of slices 2-4:
    with ``str``, a boolean label passed this check and then matched no row)."""
    return label_text(a) == label_text(b)


def start(session: Session, body: dict[str, Any], who: Who) -> dict[str, Any]:
    """Validate, record the derivation and its job (queued). The caller dispatches."""
    settings = get_settings()
    version = session.get(Version, body["source_version_id"])
    if version is None:
        raise _refuse(404, "version_not_found", f"No version {body['source_version_id']}.")
    if version.state != VersionState.COMPLETED:
        raise _refuse(
            409,
            "version_not_complete",
            f"Version {version.id} is {version.state}; derive from a completed version.",
            version_id=version.id,
        )
    group = body.get("group_column") or ROW_KEY
    for role, column in (
        ("text_column", body["text_column"]),
        ("label_column", body["label_column"]),
        ("group_column", group),
    ):
        if column != ROW_KEY and column not in version.column_roles:
            raise _refuse(
                422,
                "label_column_missing",
                f"The version has no column {column!r} ({role}). It has: "
                + ", ".join(sorted(c for c in version.column_roles if not c.startswith("_dw_")))
                + ".",
                column=column,
                role=role,
                version_id=version.id,
            )
    classes = _distinct(version, body["label_column"])
    positives = list(body["positive_values"])
    if (
        len(classes) != 2
        or not all(any(_same(p, c) for c in classes) for p in positives)
        or all(any(_same(p, c) for p in positives) for c in classes)
    ):
        raise _refuse(
            422,
            "label_column_not_binary",
            f"The label column {body['label_column']!r} must hold exactly two classes, and the "
            f"positive values {positives} must name one of them (not both); it holds "
            f"{sorted(str(c) for c in classes)}" + (" and more." if len(classes) > 2 else "."),
            column=body["label_column"],
            classes=sorted(str(c) for c in classes),
            version_id=version.id,
        )
    cap = settings.rule_derivation_max_signals
    if int(body["max_signals"]) > cap:
        raise RuleSetError(
            413,
            "cap_exceeded",
            f"RULE_DERIVATION_MAX_SIGNALS allows {cap}; this asks for {body['max_signals']}.",
            {"setting": "RULE_DERIVATION_MAX_SIGNALS", "limit": cap, "actual": body["max_signals"]},
        )
    derivation_id = new_id("rdv")
    params = {
        "text_column": body["text_column"],
        "label_column": body["label_column"],
        "positive_values": positives,
        "group_column": group,
        "floor": float(body["floor"]),
        "max_signals": int(body["max_signals"]),
        "min_support": int(body["min_support"]),
        "held_out_fraction": float(body["held_out_fraction"]),
        "seed": int(body["seed"]),
        "behaviour_key": body["behaviour_key"],
        "name": body.get("name") or f"derived-{derivation_id.split('_', 1)[1]}",
        "min_held_out_per_class": settings.rule_derivation_min_held_out_per_class,
        "max_source_rows": settings.rule_derivation_max_source_rows,
    }
    job = Job(
        id=new_id("job"),
        kind=KIND,
        status="queued",
        progress=0.0,
        params={"derivation_id": derivation_id, "version_id": version.id},
        started_by=who.who,
        started_by_origin=who.origin,
    )
    session.add(job)
    session.flush()
    session.add(
        RuleDerivation(
            id=derivation_id,
            job_id=job.id,
            source_version_id=version.id,
            params=params,
            params_sha256=digest_text(params),
            method=derivation.METHOD,
            method_version=derivation.METHOD_VERSION,
            status="queued",
            created_by=who.who,
            created_by_origin=who.origin,
        )
    )
    session.commit()
    return {
        "derivation_id": derivation_id,
        "job_id": job.id,
        "status": "queued",
        "next": next_for(
            "dataworks_get_rule_derivation",
            {"derivation_id": derivation_id},
            "read the figures when the job completes (dataworks_job_status polls it)",
        ),
    }


def read_rows(version: Version, params: dict[str, Any]) -> tuple[list[str], list[bool], list[str]]:
    """Text, label (positive?) and group per row; a seeded deterministic sample above the cap."""
    files = files_param(version_files(version.splits))
    text_c, label_c, group_c = params["text_column"], params["label_column"], params["group_column"]
    con = connect()
    try:
        rows = con.execute(
            f'SELECT "{text_c}", CAST("{label_c}" AS VARCHAR), CAST("{group_c}" AS VARCHAR) '  # noqa: S608
            f'FROM read_parquet(?) ORDER BY hash("{ROW_KEY}", ?), "{ROW_KEY}" LIMIT ?',
            [files, int(params["seed"]), int(params["max_source_rows"])],
        ).fetchall()
    finally:
        con.close()
    positives = {label_text(v) for v in params["positive_values"]}
    texts = [r[0] if isinstance(r[0], str) else "" for r in rows]
    labels = [r[1] in positives for r in rows]
    groups = [str(r[2]) for r in rows]
    return texts, labels, groups


def _finish(session: Session, row: RuleDerivation, **values: Any) -> None:
    for key, value in values.items():
        setattr(row, key, value)
    row.completed_at = utc_now()
    session.commit()


def run(session: Session, derivation_id: str, job_id: str) -> dict[str, Any]:
    """The job body: derive, measure, store. Raises :class:`OperatorCancelled` on a cancel."""
    row = session.get(RuleDerivation, derivation_id)
    assert row is not None
    version = session.get(Version, row.source_version_id)
    assert version is not None
    row.status = "running"
    session.commit()
    params = dict(row.params)
    check = CancelCheck(job_id)
    texts, labels, groups = read_rows(version, params)
    record_progress(job_id, progress=20.0, message=f"Read {len(texts):,} rows.")
    provenance = {
        "kind": "derived",
        "derivation_id": row.id,
        "method": row.method,
        "method_version": row.method_version,
        "source_version_id": str(row.source_version_id),
        "label_column": params["label_column"],
        "seed": int(params["seed"]),
        "params_sha256": row.params_sha256,
    }
    derived = derivation.sequential_cover(
        texts,
        labels,
        groups,
        derivation.Params(
            floor=float(params["floor"]),
            max_signals=int(params["max_signals"]),
            min_support=int(params["min_support"]),
            held_out_fraction=float(params["held_out_fraction"]),
            seed=int(params["seed"]),
            min_held_out_per_class=int(params["min_held_out_per_class"]),
        ),
        name=params["name"],
        behaviour_key=params["behaviour_key"],
        provenance=provenance,
        label_column=params["label_column"],
        cancel=lambda: check.raise_if_cancelled("Stopped between literals."),
    )
    canonical = (
        rule_set.canonical_bytes(derived.document).decode("utf-8")
        if derived.document is not None
        else None
    )
    from ..publishing.check_inputs import source_facts

    inherited = sorted({f.current.licence_class.value for f in source_facts(session, version)})
    _finish(
        session,
        row,
        status="completed",
        state=derived.state,
        result=derived.result,
        draft_canonical=canonical,
        draft_sha256=rule_set.digest(derived.document) if derived.document is not None else None,
        inherited_licence_classes=inherited,
    )
    logger.info(
        "rule derivation %s: %s, %d literal(s), %d held-out row(s)",
        row.id,
        derived.state,
        len(derived.result.get("literals", [])),
        derived.result["rows"]["held_out"],
    )
    return {"derivation_id": row.id, "state": derived.state}


def fail(session: Session, derivation_id: str, code: str, message: str) -> None:
    session.rollback()
    row = session.get(RuleDerivation, derivation_id, populate_existing=True)
    if row is not None and row.status not in TERMINAL:
        _finish(session, row, status="failed", error={"code": code, "message": message})


def cancelled(session: Session, derivation_id: str) -> None:
    session.rollback()
    row = session.get(RuleDerivation, derivation_id, populate_existing=True)
    if row is not None and row.status not in TERMINAL:
        _finish(session, row, status="cancelled")


def out(session: Session, derivation_id: str) -> dict[str, Any]:
    row = session.get(RuleDerivation, derivation_id)
    if row is None:
        raise RuleSetError(
            404,
            "derivation_not_found",
            f"No rule derivation {derivation_id}.",
            {"derivation_id": derivation_id},
        )
    status, error = row.status, row.error
    job = session.get(Job, row.job_id)
    # A job the janitor failed (or that was cancelled before it started) never wrote this row:
    # read its end from the job, never show a dead derivation as still running.
    if row.status not in TERMINAL and job is not None and job.status in TERMINAL_STATUSES:
        status = job.status
        error = error or {"code": "job_" + job.status, "message": job.error or job.status}
    draft = json.loads(row.draft_canonical) if row.draft_canonical else None
    body: dict[str, Any] = {
        "id": row.id,
        "job_id": row.job_id,
        "source_version_id": str(row.source_version_id),
        "method": row.method,
        "method_version": row.method_version,
        "params": row.params,
        "params_sha256": row.params_sha256,
        "status": status,
        "state": row.state,
        "result": row.result,
        "draft": draft,
        "draft_sha256": row.draft_sha256,
        "inherited_licence_classes": row.inherited_licence_classes,
        "error": error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }
    if draft is not None:
        body["next"] = next_for(
            "dataworks_save_rule_set",
            {"document": "<the draft above>"},
            "save the draft as a rule set (it keeps its provenance); nothing is saved until then",
        )
    elif status not in TERMINAL:
        body["next"] = next_for("dataworks_job_status", {"job_id": row.job_id}, "wait for the job")
    else:
        body["next"] = next_for(
            "dataworks_derive_rule_set",
            {"source_version_id": str(row.source_version_id)},
            "derive again with other parameters (a lower floor or support)",
        )
    return body


def check_provenance(session: Session, document: rule_set.RuleSetDocument) -> None:
    """Save's provenance stage (FR-016.31): the derivation exists and every field agrees."""
    provenance = document.provenance
    if provenance is None:
        return
    row = session.get(RuleDerivation, provenance.derivation_id)
    if row is None:
        raise RuleSetError(
            404,
            "derivation_not_found",
            f"provenance names derivation {provenance.derivation_id}, which does not exist.",
            {"derivation_id": provenance.derivation_id},
            next_tool="dataworks_derive_rule_set",
            why="derive a rule set first; its result carries the provenance to copy",
        )
    expected = {
        "method": row.method,
        "method_version": row.method_version,
        "source_version_id": str(row.source_version_id),
        "label_column": row.params["label_column"],
        "seed": int(row.params["seed"]),
        "params_sha256": row.params_sha256,
    }
    given = {k: getattr(provenance, k) for k in expected}
    wrong = sorted(k for k in expected if given[k] != expected[k])
    if wrong:
        raise RuleSetError(
            422,
            "provenance_mismatch",
            f"provenance disagrees with derivation {row.id} at {', '.join(wrong)}; copy it from "
            "the derivation's draft unchanged.",
            {"derivation_id": row.id, "fields": wrong},
            next_tool="dataworks_get_rule_derivation",
            next_args={"derivation_id": row.id},
            why="read the draft whose provenance to copy",
        )
