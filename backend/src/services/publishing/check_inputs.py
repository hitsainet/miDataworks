"""Assemble :class:`checks.CheckInputs` and the manifest's source and labeler facts from the
other features' records (FR-008.15, FR-008.59; FTDD 008 section 2.3).

Reads only. Every value comes from a real column (``test_reads_real_columns.py``); a fact a source
does not give is ``None`` or ``not_checked``, never a plausible default.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models.dataset import Dataset
from ...models.publish import ModelTermsNote
from ...models.source import Source, SourceAnnotation
from ...models.source_enums import AnnotationKind
from ...models.version import Version
from ..duck import connect, files_param, top_level_columns
from ..version_lineage import lineage_bindings, lineage_versions
from . import feature_seams
from .checks import (
    CheckInputs,
    LabelerCheck,
    ModelTerms,
    SourceLicence,
    TokenScope,
    gather_curation_findings,
)
from .feature_seams import CHECKED, NOT_CHECKED, LabelerFinding
from .licence_table import TABLE, AnnotationRef, Classification, LicenceClass, classify


@dataclass(frozen=True)
class SourceFacts:
    source: Source
    current: Classification
    built: Classification
    terms_recorded: bool


def _annotations(session: Session, source_id: str) -> list[SourceAnnotation]:
    return list(
        session.execute(
            select(SourceAnnotation)
            .where(
                SourceAnnotation.source_id == source_id,
                SourceAnnotation.kind.in_((AnnotationKind.TERMS, AnnotationKind.LICENCE)),
            )
            .order_by(SourceAnnotation.created_at, SourceAnnotation.id)
        ).scalars()
    )


def _refs(rows: Sequence[SourceAnnotation], before: datetime | None = None) -> list[AnnotationRef]:
    out: list[AnnotationRef] = []
    for a in rows:
        if before is not None and a.created_at > before:
            continue
        assert a.redistribution is not None  # CHECK redistribution_required
        out.append(AnnotationRef(a.redistribution, a.id))
    return out


def source_ids(version: Version) -> list[str]:
    """Every source the version reached through its lineage (002's manifest, FR-002.8)."""
    manifest = json.loads(version.manifest)
    return [str(s["source_id"]) for s in manifest["sources"]]


def source_facts(session: Session, version: Version) -> list[SourceFacts]:
    facts: list[SourceFacts] = []
    for sid in source_ids(version):
        source = session.get(Source, sid)
        if source is None:
            raise LookupError(f"version {version.id} names source {sid}, which does not exist")
        rows = _annotations(session, sid)
        facts.append(
            SourceFacts(
                source=source,
                current=classify(source.licence_raw, _refs(rows)),
                built=classify(source.licence_raw, _refs(rows, before=version.created_at)),
                terms_recorded=any(a.kind == AnnotationKind.TERMS for a in rows),
            )
        )
    return facts


def _gated(value: str | None) -> bool | None:
    if value is None:
        return None
    return value not in ("false", "0", "no")


def manifest_source(f: SourceFacts) -> dict[str, Any]:
    """One ``Source`` of the contract (M-5)."""
    s = f.source
    if s.kind == "hf":
        assert s.repo_id is not None and s.resolved_commit is not None
        origin: dict[str, Any] = {
            "kind": "hub",
            "repo_id": s.repo_id,
            "config": s.config,
            "split": s.split_selection,
            "revision": s.resolved_commit,
        }
    else:
        assert s.content_hash is not None
        origin = {"kind": "upload", "filename": s.display_name, "content_sha256": s.content_hash}
    raw = s.licence_raw
    if raw is not None and not isinstance(raw, str | list):
        raw = str(raw)
    return {
        "source_id": s.id,
        "origin": origin,
        "licence": {
            "displayed": s.licence_display,
            "raw": [str(x) for x in raw] if isinstance(raw, list) else raw,
            "origin": (
                "operator" if f.current.decided_by == "annotation" else (s.licence_origin or "none")
            ),
            "licence_class": f.current.licence_class.value,
            "table_version": TABLE.version,
        },
        "terms_status": "recorded" if f.terms_recorded else "not_recorded",
        "gated": _gated(s.gated),
        "extensions": {},
    }


def bound_runs(session: Session, version: Version) -> tuple[list[str], list[str]]:
    """(label runs, generation runs) bound ANYWHERE in the version's lineage.

    A re-split or merge binds nothing itself; the runs that generated and labelled its rows are
    bound on its ancestors. Reading only the version's own bindings described a synthetic,
    model-judged dataset as having neither (live, 2026-10-09)."""
    found = lineage_bindings(lineage_versions(session, version))
    return found.label_runs, found.generation_runs


def generated_row_count(version: Version) -> int:
    """Rows whose ``_dw_origin`` is ``generated`` in the version's own split files: a fact read
    from the rows, needing no lineage at all."""
    from ...core.storage import resolve_under_data_dir

    files = [resolve_under_data_dir(str(s["path"])) for s in version.splits]
    if not files:
        return 0
    con = connect()
    try:
        if "_dw_origin" not in top_level_columns(con, files):
            return 0
        row = con.execute(
            "SELECT count(*) FROM read_parquet(?) WHERE _dw_origin = 'generated'",
            [files_param(files)],
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        con.close()


@dataclass(frozen=True)
class ModelFindings:
    labelers: LabelerFinding
    #: Every model C-4 must read a terms note for (labelers and generators).
    models: LabelerFinding
    generators: LabelerFinding


def model_findings(session: Session, version: Version) -> ModelFindings:
    """Labelers, generators and every model they used, across the version's lineage."""
    label_runs, generation_runs = bound_runs(session, version)
    labelers = feature_seams.labelers_for(label_runs, session=session)
    generators = feature_seams.generators_for(generation_runs, session=session)
    if generators.status != CHECKED or labelers.status != CHECKED:
        return ModelFindings(labelers, LabelerFinding(NOT_CHECKED), generators)
    models = sorted(set(labelers.model_ids) | set(generators.model_ids))
    return ModelFindings(labelers, LabelerFinding(CHECKED, labelers.labelers, models), generators)


def lineage_record(
    session: Session, version: Version, generators: LabelerFinding, generated_rows: int
) -> dict[str, Any]:
    """The lineage facts the record carries beside the contract's own fields (the manifest's
    ``extensions["lineage"]``): ancestor versions, the generation runs and their models, and the
    generated row count. The card's record section is rendered from these."""
    ancestors = lineage_versions(session, version)[1:]
    return {
        "ancestor_versions": [
            {"version_id": str(v.id), "number": int(v.number)} for v in ancestors
        ],
        "generated_rows": int(generated_rows),
        "generators": [
            {
                "generation_run_id": str(g["generation_run_id"]),
                "model_id": g.get("model_id"),
                "revision": g.get("revision"),
                "mode": g.get("mode"),
            }
            for g in generators.labelers
        ],
    }


def latest_terms(session: Session, model_ids: Sequence[str]) -> list[ModelTerms]:
    out: list[ModelTerms] = []
    for model_id in model_ids:
        note = session.execute(
            select(ModelTermsNote)
            .where(ModelTermsNote.model_id == model_id)
            .order_by(ModelTermsNote.noted_at.desc(), ModelTermsNote.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        out.append(ModelTerms(model_id, note.training_on_outputs if note is not None else None))
    return out


def labeler_checks(
    session: Session, labelers: LabelerFinding
) -> tuple[list[LabelerCheck], list[dict[str, Any]]]:
    checks: list[LabelerCheck] = []
    refs: list[dict[str, Any]] = []
    for lab in labelers.labelers:
        fingerprint = str(lab["fingerprint"])
        cal = feature_seams.calibration_status(fingerprint, session=session)
        checks.append(
            LabelerCheck(
                fingerprint=fingerprint,
                name=str(lab["model_id"] or fingerprint[:12]),
                pinned=bool(lab["pinned"]),
                revision_reported=bool(lab["revision_reported"]),
                calibration=cal,
            )
        )
        refs.append(
            {
                "labeler_fingerprint": fingerprint,
                "status": "recorded" if cal.recorded else "none_recorded",
                "record_id": cal.record_id,
                "verdict": cal.verdict,
                "rule": cal.rule,
                "auroc": cal.auroc,
                "calibration_set": (
                    {**cal.calibration_set, "rows_shipped": False} if cal.calibration_set else None
                ),
            }
        )
    return checks, refs


@dataclass(frozen=True)
class Assembled:
    inputs: CheckInputs
    sources: list[SourceFacts]
    labelers: list[dict[str, Any]]
    calibration: list[dict[str, Any]]
    dataset: Dataset
    #: ``lineage_record``: written to the manifest's ``extensions["lineage"]``.
    lineage: dict[str, Any]


def assemble(
    session: Session,
    version: Version,
    *,
    label_column: str | None,
    visibility: str,
    token_scope: TokenScope,
) -> Assembled:
    assert visibility in ("private", "public")
    facts = source_facts(session, version)
    held_out = [str(s["name"]) for s in version.splits if s["held_out"]]
    split_inputs = [(version.id, str(s["name"])) for s in version.splits]
    leakage, warnings = gather_curation_findings(
        split_inputs,
        held_out=held_out,
        group_column=None,
        label_column=label_column,
        session=session,
    )
    found = model_findings(session, version)
    labelers, models = found.labelers, found.models
    generated = generated_row_count(version)
    checks_list, calibration = labeler_checks(session, labelers)
    terms = latest_terms(session, models.model_ids) if models.status == CHECKED else []
    dataset = session.get(Dataset, version.dataset_id)
    assert dataset is not None
    inputs = CheckInputs(
        visibility=visibility,  # type: ignore[arg-type]
        sources=[
            SourceLicence(
                source_id=f.source.id,
                name=f.source.display_name,
                displayed=f.source.licence_display,
                current_class=f.current.licence_class,
                built_class=f.built.licence_class,
            )
            for f in facts
        ],
        token_scope=token_scope,
        held_out_splits=held_out,
        leakage=leakage,
        models=models,
        model_terms=terms,
        audit=feature_seams.audit_status(version.id, session=session),
        labelers=checks_list,
        labeler_status=labelers.status,
        warnings=warnings,
        diversity=feature_seams.diversity_for(version.id, session=session),
        generated_rows=generated,
        generators=list(found.generators.labelers),
    )
    lineage = lineage_record(session, version, found.generators, generated)
    return Assembled(inputs, facts, labelers.labelers, calibration, dataset, lineage)


def any_forbids(facts: Sequence[SourceFacts]) -> bool:
    return any(f.current.licence_class is LicenceClass.FORBIDS for f in facts)
