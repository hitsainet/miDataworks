"""TRL-ready exports and miForge set exports (FR-008.25–008.29, FR-008.41, FR-008.43, FR-008.68;
FTID 008 sections 7.4, 7.5).

An export writes, under ``exports/<export_id>/``:

- one file per split with EXACTLY the contract's columns (GRPO: plus operator-chosen extras), as
  Parquet or JSONL (``json.dumps(..., ensure_ascii=False)``; booleans stay booleans);
- ``<split>.row_keys.parquet`` beside each, aligned by row index (``_dw_row_key``,
  ``_dw_occurrence``) so 002's logical digest is checkable without the key rules;
- ``midataworks-dataset-version.json``, the handoff manifest (FR-008.29).

Effective labels and omissions are applied exactly as for a publish (``projection``). Held-out
splits are written as their own files, marked ``held_out`` and ``evaluation_only``; no code path
merges them into another split (FR-008.28). Before any file is written the export calls feature
004's validator in check mode; while 004 is not built that answer is ``not_checked`` and the
export is REFUSED (``trl_validator_unavailable``) — a missing validation is never a pass.

Exports write nothing to the Hub and need no approval (FR-008.52). A miForge set is delivered by
publishing its version to a private repository (FR-008.68); the local directory is for inspection.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy.orm import Session

from ...core.clock import utc_now
from ...core.errors import AppError, NotFoundError
from ...core.ids import new_id
from ...core.storage import atomic_write_bytes, export_dir, resolve_under_data_dir, staged_path
from ...models.job import Job
from ...models.publish import Export, ExportStatus, ExportTarget
from ...models.version import Version
from ..identity import bytes_sha256, file_sha256, logical_digest
from ..publishing import feature_seams
from ..publishing.build_service import check_label_column, version_or_refuse
from ..publishing.card import caveats_from_outcomes
from ..publishing.check_inputs import assemble, manifest_source
from ..publishing.checks import evaluate_checks
from ..publishing.manifest_builder import MANIFEST_FILE, build_document, manifest_bytes
from ..publishing.projection import Counters, apply_effective_labels
from .trl_contracts import CONTRACTS, Variant, formable_variant

FORMATS = ("parquet", "jsonl")

#: miForge set kinds (FR-008.41) as TRL-style column contracts (FTID 008 section 7.5).
MIFORGE_SETS: dict[str, tuple[Variant, ...]] = {
    "prompt_set": (Variant(("prompt",)),),
    "retention_set": (Variant(("prompt",)),),
    "corpus": (Variant(("text",)), Variant(("messages",))),
    "preference_pairs": (Variant(("prompt", "chosen", "rejected")),),
    "test_set": (
        Variant(("prompt", "reference", "match")),
        Variant(("prompt", "grader_config_sha256")),
    ),
}


class ExportRefusal(AppError):
    status_code = 422


def _columns(version: Version) -> set[str]:
    return {c for c in version.column_roles if not c.startswith("_dw_")}


def plan_columns(version: Version, params: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """The exact columns to write, or a refusal naming the missing one (EC-11)."""
    columns = _columns(version)
    if params["target"] == ExportTarget.TRL:
        trl_type = params["trl_type"]
        if trl_type not in CONTRACTS:
            raise ExportRefusal(f"Unknown TRL type {trl_type!r}.", code="trl_type_unknown")
        variant, missing = formable_variant(trl_type, columns)
        contract = CONTRACTS[trl_type]
        extras = list(params.get("extra_columns") or [])
        if extras and not contract.extra_columns_allowed:
            raise ExportRefusal(
                f"{trl_type} takes exactly its own columns; extra columns are only for GRPO.",
                code="trl_extra_columns_not_allowed",
            )
        unknown = [c for c in extras if c not in columns]
        if unknown:
            raise ExportRefusal(
                f"The version has no column {unknown[0]!r}.",
                code="column_unknown",
                details={"missing": unknown},
            )
        if variant is None:
            raise ExportRefusal(
                f"This version cannot form a {trl_type.upper()} dataset: it has no column "
                f"{missing[0]!r}. Add it with a recipe step, or choose another type.",
                code="trl_type_unformable",
                details={"missing": missing, "trl_type": trl_type, "contract": contract.source},
            )
        return list(variant.columns) + [c for c in extras if c not in variant.columns], {
            "trl_version": contract.trl_version,
            "variant": variant,
        }
    kind = params["miforge_set_kind"]
    if kind not in MIFORGE_SETS:
        raise ExportRefusal(f"Unknown miForge set kind {kind!r}.", code="miforge_set_kind_unknown")
    for variant in MIFORGE_SETS[kind]:
        if all(c in columns for c in variant.columns):
            return list(variant.columns), {"trl_version": None, "variant": variant}
    first = MIFORGE_SETS[kind][0]
    missing = [c for c in first.columns if c not in columns]
    raise ExportRefusal(
        f"This version cannot form a miForge {kind}: it has no column {missing[0]!r}.",
        code="miforge_set_unformable",
        details={"missing": missing},
    )


def _validate_with_004(session: Session, version: Version, params: dict[str, Any]) -> None:
    """FR-008.26: 004's validator in check mode; ``not_checked`` refuses."""
    target_type = params.get("trl_type") or {
        "preference_pairs": "dpo",
        "corpus": "sft",
    }.get(params.get("miforge_set_kind") or "", "grpo_prompt")
    finding = feature_seams.validate_trl(version.id, target_type, session=session)
    if finding.status == feature_seams.NOT_CHECKED:
        raise AppError(
            "The TRL format validator (feature 004) is not installed in this build, and an export "
            "is never written unvalidated. Wait for feature 004, then export again.",
            code="trl_validator_unavailable",
            status_code=409,
            details={"owner": "004"},
        )
    if not finding.valid:
        raise ExportRefusal(
            "The version fails the TRL format validator: "
            + "; ".join(str(f) for f in finding.failures[:3]),
            code="trl_validation_failed",
            details={"failures": finding.failures[:20]},
        )


def request_export(
    session: Session, params: dict[str, Any], *, started_by: str, origin: str
) -> tuple[Export, Job]:
    if params["target"] not in (ExportTarget.TRL, ExportTarget.MIFORGE_SET):
        raise ExportRefusal(
            f"Export target {params['target']!r} is not available in this build.",
            code="export_target_unavailable",
        )
    if params.get("format", "parquet") not in FORMATS:
        raise ExportRefusal("format must be parquet or jsonl", code="export_format_invalid")
    version = version_or_refuse(session, params["version_id"])
    check_label_column(version, params.get("label_column"))
    columns, extra = plan_columns(version, params)
    _validate_with_004(session, version, params)
    export_id = new_id("exp")
    job = Job(
        id=new_id("job"),
        kind="export",
        status="queued",
        progress=0.0,
        params={"export_id": export_id},
        started_by=started_by,
        started_by_origin=origin,
    )
    session.add(job)
    session.flush()
    row = Export(
        id=export_id,
        job_id=job.id,
        target=params["target"],
        version_id=version.id,
        params={**params, "columns": columns},
        trl_version=extra["trl_version"],
        status=ExportStatus.QUEUED,
        started_by=started_by,
        started_by_origin=origin,
    )
    session.add(row)
    session.commit()
    return row, job


def _check_booleans(table: pa.Table, variant: Variant) -> None:
    for name in variant.boolean:
        if not pa.types.is_boolean(table.schema.field(name).type):
            raise ExportRefusal(
                f"Column {name!r} must hold booleans for TRL; it holds {table.schema.field(name).type}.",
                code="trl_column_type",
                details={"column": name},
            )
    for name in variant.boolean_list:
        t = table.schema.field(name).type
        if not (pa.types.is_list(t) and pa.types.is_boolean(t.value_type)):
            raise ExportRefusal(
                f"Column {name!r} must hold lists of booleans for TRL; it holds {t}.",
                code="trl_column_type",
                details={"column": name},
            )


def write_split(
    source: Path,
    out_dir: Path,
    file_stem: str,
    columns: list[str],
    fmt: str,
    variant: Variant,
    label_column: str | None,
    resolved: dict[str, dict[str, Any]] | None,
    counters: Counters,
) -> tuple[Path, Path]:
    keep = [*columns, "_dw_row_key", "_dw_occurrence"]
    handle = pq.ParquetFile(source)
    parts: list[pa.Table] = []
    for batch in handle.iter_batches(
        columns=list(dict.fromkeys(keep + ([label_column] if label_column else [])))
    ):
        projected = apply_effective_labels(batch, label_column, resolved, counters)
        parts.append(pa.Table.from_batches([projected]))
    table = (
        pa.concat_tables(parts)
        if parts
        else handle.schema_arrow.empty_table().select(list(dict.fromkeys(keep)))
    )
    _check_booleans(table, variant)
    data = table.select(columns)
    keys = table.select(["_dw_row_key", "_dw_occurrence"])
    data_path = out_dir / f"{file_stem}.{fmt}"
    keys_path = out_dir / f"{file_stem}.row_keys.parquet"
    if fmt == "parquet":
        with staged_path(data_path) as staged:
            pq.write_table(data, staged, compression="zstd")
    else:
        lines = [json.dumps(row, ensure_ascii=False) for row in data.to_pylist()]
        atomic_write_bytes(data_path, ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8"))
    with staged_path(keys_path) as staged:
        pq.write_table(keys, staged, compression="zstd")
    return data_path, keys_path


def run_export(session: Session, export_id: str) -> dict[str, Any]:
    row = session.get(Export, export_id)
    if row is None:
        raise NotFoundError(f"No export {export_id}.", code="export_not_found")
    row.status = ExportStatus.RUNNING
    session.commit()
    try:
        result = _run(session, row)
    except AppError as exc:
        session.rollback()
        row = session.get(Export, export_id, populate_existing=True)
        assert row is not None
        row.status = ExportStatus.FAILED
        row.error = {"code": exc.code, "message": exc.message, "details": exc.details}
        row.completed_at = utc_now()
        session.commit()
        return {"status": "failed", "error": row.error}
    return result


def _run(session: Session, row: Export) -> dict[str, Any]:
    assert row.version_id is not None
    params = dict(row.params)
    version = version_or_refuse(session, row.version_id)
    columns, extra = plan_columns(version, params)
    _validate_with_004(session, version, params)
    variant: Variant = extra["variant"]
    fmt = params.get("format", "parquet")
    label_column = params.get("label_column")
    out = export_dir(row.id)
    out.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, Any]] = []
    totals = Counters()
    splits: list[dict[str, Any]] = []
    review = feature_seams.effective_labels_available()
    for split in version.splits:
        source = resolve_under_data_dir(split["path"])
        resolved = None
        if review and label_column:
            keys = pq.read_table(source, columns=["_dw_row_key"]).column(0).to_pylist()
            resolved = feature_seams.resolve_effective_labels(version.id, None, None, keys)
        counters = Counters()
        data_path, keys_path = write_split(
            source,
            out,
            Path(split["path"]).stem,
            columns,
            fmt,
            variant,
            label_column,
            resolved,
            counters,
        )
        totals.omitted_flagged_unresolved += counters.omitted_flagged_unresolved
        totals.overrides_applied += counters.overrides_applied
        rows = pq.ParquetFile(keys_path).metadata.num_rows
        for path, role in ((data_path, "data"), (keys_path, "row_keys")):
            files.append(
                {
                    "path": path.name,
                    "role": role,
                    "split": split["name"],
                    "bytes": path.stat().st_size,
                    "sha256": file_sha256(path),
                }
            )
        splits.append(
            {
                "name": split["name"],
                "path": data_path.name,
                "rows": rows,
                "bytes": data_path.stat().st_size,
                "sha256": file_sha256(data_path),
                "logical_digest": logical_digest(keys_path),
                "label_counts": dict(sorted(counters.label_counts.items())),
                "held_out": bool(split["held_out"]),
                "evaluation_only": bool(split["held_out"]),
                "extensions": {},
            }
        )
    document = export_manifest(session, version, row, params, columns, splits, totals)
    content = manifest_bytes(document)
    atomic_write_bytes(out / MANIFEST_FILE, content)
    row.files = files
    row.manifest_sha256 = bytes_sha256(content)
    row.status = ExportStatus.COMPLETED
    row.completed_at = utc_now()
    session.commit()
    return {"status": "completed", "files": files, "manifest_sha256": row.manifest_sha256}


def export_manifest(
    session: Session,
    version: Version,
    row: Export,
    params: dict[str, Any],
    columns: list[str],
    splits: list[dict[str, Any]],
    totals: Counters,
) -> dict[str, Any]:
    from ..publishing.publish_job import version_identity

    label_column = params.get("label_column")
    # Caveats as a private push would record them (C-2 does not apply to a local export).
    assembled = assemble(
        session,
        version,
        label_column=label_column,
        visibility="private",
        token_scope="write",  # noqa: S106
    )
    outcomes = [o for o in evaluate_checks(assembled.inputs) if o.check != "C-2"]
    fmt = params.get("format", "parquet")
    first = export_dir(row.id) / f"{Path(version.splits[0]['path']).stem}.parquet"
    shipped = pq.read_schema(first) if fmt == "parquet" else None
    column_list: list[dict[str, Any]] = []
    for name in columns:
        arrow_type = str(shipped.field(name).type) if shipped is not None else "json"
        semantic = (
            "label"
            if name == label_column
            else {
                "text": "text",
                "messages": "messages",
                "prompt": "prompt",
                "completion": "completion",
                "completions": "completions",
                "chosen": "chosen",
                "rejected": "rejected",
                "labels": "labels",
                "reference": "reference",
            }.get(name, "other")
        )
        values = (
            sorted({k for s in splits for k in s["label_counts"]}) if semantic == "label" else None
        )
        column_list.append(
            {
                "name": name,
                "arrow_type": arrow_type,
                "role": version.column_roles.get(name, "metadata"),
                "semantic": semantic,
                "label_values": values,
                "extensions": {},
            }
        )
    if params["target"] == ExportTarget.TRL:
        target = {
            "kind": "trl_export",
            "dataset_target_type": assembled.dataset.target_type,
            "trl_type": params["trl_type"],
            "trl_version": row.trl_version,
            "detector_role": None,
            "miforge_set_kind": None,
            "contract_reference": None,
        }
    else:
        target = {
            "kind": "miforge_set",
            "dataset_target_type": assembled.dataset.target_type,
            "trl_type": None,
            "trl_version": None,
            "detector_role": None,
            "miforge_set_kind": params["miforge_set_kind"],
            "contract_reference": {
                "type": params["miforge_set_kind"],
                "name": assembled.dataset.name,
                "version": f"v{version.number}",
            },
        }
    return build_document(
        version=version_identity(version, assembled.dataset.name),
        target=target,
        sources=[manifest_source(f) for f in assembled.sources],
        content={
            "content_kind": "rows",
            "columns": column_list,
            "splits": splits,
            "projection": {
                "label_resolver": "dw.effective-label/v1",
                "label_column": label_column,
                "omitted_excluded": totals.omitted_excluded,
                "omitted_flagged_unresolved": totals.omitted_flagged_unresolved,
                "overrides_applied": totals.overrides_applied,
            },
            "labelers": assembled.labelers,
            "calibration": assembled.calibration,
        },
        caveats=caveats_from_outcomes(outcomes),
        publication=None,
        generated_at=utc_now(),
        extensions={"lineage": assembled.lineage},
    )


def export_file(session: Session, export_id: str, name: str) -> Path:
    """A file of a completed export, confined to ``exports/<id>/`` (FTASKS 15.4)."""
    row = session.get(Export, export_id)
    if row is None or row.status != ExportStatus.COMPLETED:
        raise NotFoundError(f"No completed export {export_id}.", code="export_not_found")
    allowed = {f["path"] for f in row.files or []} | {MANIFEST_FILE}
    if name not in allowed:
        raise NotFoundError(
            f"Export {export_id} has no file {name!r}.", code="export_file_not_found"
        )
    base = export_dir(row.id)
    return resolve_under_data_dir(name, root=base)
