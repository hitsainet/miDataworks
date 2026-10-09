"""Detector sets: CRUD, length profiles, label values and check inputs (FR-009.1 - FR-009.16,
FR-009.80; FTDD 009 sections 5.1, 5.3; FTID 009 section 7.1).

Synchronous on a SQLAlchemy ``Session``: routes call it through ``AsyncSession.run_sync``, and the
send worker calls the same functions, so the check a route shows and the check a send snapshots are
one code path.

One authority per fact (FTID 009 section 1):
- label values come from 008's projection function (``apply_effective_labels``), the function that
  writes the published files, so the mapping check and the file cannot disagree (FTDD 4.3);
- shortcut warnings and leakage come from 004 (``curation_seam``); calibration verdicts from 006
  (``feature_seams.calibration_status``); labeler identities from 005 (``feature_seams.labelers_for``);
- the check RULES are ``checks.evaluate``; this module only gathers facts.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...core.config import get_settings
from ...core.ids import new_id
from ...core.storage import resolve_under_data_dir
from ...models.dataset import Dataset
from ...models.detector_results import DetectorResults, LengthProfile
from ...models.detector_send import DetectorSend
from ...models.detector_set import DetectorSet, DetectorSetRole
from ...models.enums import VersionState
from ...models.version import Version
from ..curation.audit_service import AUDITED_SYSTEM
from ..curation.errors import CurationError
from ..duck import connect, files_param, quote_ident
from ..publishing import feature_seams
from ..publishing.projection import Counters, apply_effective_labels
from . import checks, curation_seam, label_rules, length
from .checks import CheckInputs, LabelerStatus, RoleFacts
from .errors import DetectorSetError
from .role_mapping import ROLE_WORDS

logger = logging.getLogger(__name__)

UNITS: tuple[str, ...] = ("chars", "words")


@dataclass(frozen=True)
class Who:
    who: str
    origin: str


# --- versions ---------------------------------------------------------------------------------


def _version(session: Session, version_id: str) -> Version:
    try:
        key = str(uuid.UUID(str(version_id)))
    except ValueError:
        raise DetectorSetError(
            "version_incomplete", f"{version_id!r} is not a version ID.", {"version_id": version_id}
        ) from None
    version = session.get(Version, key)
    if version is None:
        raise DetectorSetError(
            "version_incomplete", f"No version {version_id}.", {"version_id": version_id}
        )
    return version


def _require_completed(version: Version) -> None:
    if version.state != VersionState.COMPLETED:
        raise DetectorSetError(
            "version_incomplete",
            f"Version {version.number} is {version.state}; a role binds a completed version. "
            "Choose a completed version.",
            {"version_id": version.id, "state": version.state},
        )


def _split(version: Version, split: str) -> dict[str, Any]:
    for s in version.splits:
        if s["name"] == split:
            return dict(s)
    raise DetectorSetError(
        "role_invalid",
        f"Version {version.number} has no split {split!r}; it has "
        + ", ".join(repr(s["name"]) for s in version.splits)
        + ".",
        {"split": split, "splits": [s["name"] for s in version.splits]},
    )


def _require_column(version: Version, column: str, what: str) -> None:
    if column.startswith("_dw_") or column not in version.column_roles:
        raise DetectorSetError(
            "role_invalid",
            f"Version {version.number} has no column {column!r} to use as the {what}. Choose one "
            f"of {sorted(c for c in version.column_roles if not c.startswith('_dw_'))}.",
            {"column": column},
        )


def split_path(version: Version, split: str) -> str:
    return str(resolve_under_data_dir(_split(version, split)["path"]))


# --- CRUD -------------------------------------------------------------------------------------


def _validate_label_sources(version: Version, spec: Mapping[str, Any]) -> None:
    """``label_source_columns``: the columns the label was COMPUTED FROM (D-3 excludes them).

    Each must exist in the role's version (or be one of the derived system columns the audit reads,
    ``_dw_origin`` and ``_dw_source_id``), and none may be the input column (the probe reads it, so
    it is never a source to set aside) or the label column itself."""
    declared = list(spec.get("label_source_columns") or [])
    if len(set(declared)) != len(declared):
        raise DetectorSetError(
            "role_invalid",
            "Label source columns list a column twice; name each once.",
            {"label_source_columns": declared},
        )
    for column in declared:
        if column == spec["input_column"]:
            raise DetectorSetError(
                "role_invalid",
                f"{column!r} is the input column: the probe reads it, so it cannot be set aside as "
                "a column the label was computed from.",
                {"column": column},
            )
        if column == spec["label_column"]:
            raise DetectorSetError(
                "role_invalid",
                f"{column!r} is the label column itself; name the columns the label was computed "
                "from.",
                {"column": column},
            )
        if column in AUDITED_SYSTEM:
            # the derived system columns the shortcut audit READS (every version has them); a
            # label computed from them may set them aside like any other source column
            continue
        _require_column(version, column, "label source column")


def _validate_role(session: Session, spec: Mapping[str, Any]) -> Version:
    version = _version(session, spec["version_id"])
    _require_completed(version)
    _split(version, spec["split"])
    _require_column(version, spec["input_column"], "input column")
    _require_column(version, spec["label_column"], "label column")
    if spec.get("pair_column"):
        _require_column(version, spec["pair_column"], "pair column")
    _validate_label_sources(version, spec)
    if spec["role"] == "calibration_negatives" and not spec.get("negatives_basis"):
        raise DetectorSetError(
            "role_invalid",
            "Calibration negatives record their basis: labeler-filtered (name the labeler and the "
            "rule), human-labelled (name the label column, the negative values, who labelled them "
            "and the rule) or assumed negative (FR-009.6).",
        )
    basis = spec.get("negatives_basis") or {}
    if basis.get("kind") == "human_labelled":
        _validate_human_basis(session, version, spec, basis)
    return version


def _validate_human_basis(
    session: Session, version: Version, spec: Mapping[str, Any], basis: Mapping[str, Any]
) -> None:
    """A human-labelled basis must describe THIS role: its label column, exactly the values its
    mapping sends to negative, and a label column no labeler step wrote (FR-009.6)."""
    if basis.get("label_column") != spec["label_column"]:
        raise DetectorSetError(
            "role_invalid",
            f"The human-labelled basis names label column {basis.get('label_column')!r}, but the "
            f"role reads {spec['label_column']!r}; name the column the negatives were selected by.",
            {"basis_label_column": basis.get("label_column"), "label_column": spec["label_column"]},
        )
    named = {label_rules.mapping_key(v) for v in basis.get("negative_values") or []}
    mapped = {
        label_rules.mapping_key(k)
        for k, target in dict(spec["label_mapping"]).items()
        if target == "negative"
    }
    if named != mapped:
        raise DetectorSetError(
            "role_invalid",
            f"The human-labelled basis selects negatives by {sorted(named)}, but the role's mapping "
            f"sends {sorted(mapped)} to negative; they must be the same values.",
            {"negative_values": sorted(named), "mapped_negative": sorted(mapped)},
        )
    from ..curation import label_columns

    written = label_columns.resolve(session, version.id)
    if written.label == spec["label_column"] or spec["label_column"] in written.derived:
        raise DetectorSetError(
            "role_invalid",
            f"Label column {spec['label_column']!r} was written by a labeler step in version "
            f"{version.number}, so these negatives are model-labelled: record a labeler-filtered "
            "basis naming the labeler identity.",
            {"label_column": spec["label_column"]},
        )


#: Basis fields that only a human_labelled basis carries; omitted from the record when absent.
_HUMAN_FIELDS = ("label_column", "negative_values", "labelled_by")


def _basis_record(basis: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not basis:
        return None
    record = dict(basis)
    if record.get("kind") == "human_labelled":
        return {k: v for k, v in record.items() if v is not None}
    return {k: v for k, v in record.items() if not (k in _HUMAN_FIELDS and v is None)}


def _insert_roles(
    session: Session, set_row: DetectorSet, roles: Sequence[Mapping[str, Any]]
) -> list[DetectorSetRole]:
    rows: list[DetectorSetRole] = []
    ood = 0
    for spec in roles:
        version = _validate_role(session, spec)
        given = spec.get("position")
        position = int(given) if given is not None else 0
        if spec["role"] == "ood_eval":
            position = int(given) if given is not None else ood
            ood += 1
        row = DetectorSetRole(
            id=new_id("dsr"),
            set_id=set_row.id,
            role=spec["role"],
            position=position,
            version_id=version.id,
            split=spec["split"],
            input_column=spec["input_column"],
            label_column=spec["label_column"],
            label_mapping={str(k): str(v) for k, v in dict(spec["label_mapping"]).items()},
            pair_column=spec.get("pair_column") or None,
            label_source_columns=[str(c) for c in spec.get("label_source_columns") or []],
            negatives_basis=_basis_record(spec.get("negatives_basis")),
            display_name=str(spec.get("display_name") or ""),
        )
        session.add(row)
        rows.append(row)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        if "uq_dw_detector_set_roles_single" in str(exc.orig):
            raise DetectorSetError(
                "roles_incomplete",
                "A detector set has exactly one training, one in-distribution test and one "
                "calibration negatives role (FR-009.2).",
            ) from None
        raise
    return rows


def _monitored(
    session: Session, ref: Mapping[str, Any] | None, roles: Sequence[DetectorSetRole]
) -> dict[str, Any] | None:
    """Resolve the monitored reference; default the first-bound OOD role (T-45)."""
    if ref is None:
        ood = sorted((r for r in roles if r.role == "ood_eval"), key=lambda r: r.position)
        return {"kind": "role", "role_id": ood[0].id} if ood else None
    if ref["kind"] == "role":
        if ref.get("role_id"):
            if not any(r.id == ref["role_id"] for r in roles):
                raise DetectorSetError(
                    "monitored_ref_missing", f"Role {ref['role_id']} is not in this set."
                )
            return {"kind": "role", "role_id": ref["role_id"]}
        index = ref.get("role_index")
        if index is None or not 0 <= int(index) < len(roles):
            raise DetectorSetError(
                "monitored_ref_missing", "Name the monitored role by its position in the role list."
            )
        return {"kind": "role", "role_id": roles[int(index)].id}
    version = _version(session, ref["version_id"])
    _require_completed(version)
    _split(version, ref["split"])
    _require_column(version, ref["column"], "monitored column")
    return {
        "kind": "version",
        "version_id": version.id,
        "split": ref["split"],
        "column": ref["column"],
    }


def create_set(session: Session, body: Mapping[str, Any], who: Who) -> DetectorSet:
    if session.execute(select(DetectorSet.id).where(DetectorSet.name == body["name"])).first():
        raise DetectorSetError(
            "name_taken", f"A detector set named {body['name']!r} exists; choose another name."
        )
    row = DetectorSet(
        id=new_id("dts"),
        name=body["name"],
        description=body.get("description") or "",
        positive_meaning=body.get("positive_meaning") or "",
        archived=False,
        created_by=who.who,
        created_by_origin=who.origin,
    )
    session.add(row)
    session.flush()
    roles = _insert_roles(session, row, body.get("roles") or [])
    row.monitored_ref = _monitored(session, body.get("monitored_ref"), roles)
    session.commit()
    logger.info("detector_set.created set=%s roles=%d who=%s", row.id, len(roles), who.who)
    return row


def get_set(session: Session, set_id: str) -> DetectorSet:
    row = session.get(DetectorSet, set_id, populate_existing=True)
    if row is None:
        raise DetectorSetError("set_not_found", f"No detector set {set_id}.")
    return row


def roles_of(session: Session, set_id: str) -> list[DetectorSetRole]:
    return list(
        session.execute(
            select(DetectorSetRole)
            .where(DetectorSetRole.set_id == set_id)
            .order_by(DetectorSetRole.role, DetectorSetRole.position, DetectorSetRole.id)
        ).scalars()
    )


def update_set(session: Session, set_id: str, body: Mapping[str, Any]) -> DetectorSet:
    """Roles change freely until sent; every send snapshots what it used (FR-009.12)."""
    row = get_set(session, set_id)
    for key in ("description", "positive_meaning"):
        if body.get(key) is not None:
            setattr(row, key, body[key])
    if body.get("roles") is not None:
        for old in roles_of(session, set_id):
            session.delete(old)
        session.flush()
        roles = _insert_roles(session, row, body["roles"])
        row.monitored_ref = _monitored(session, body.get("monitored_ref"), roles)
    elif "monitored_ref" in body and body["monitored_ref"] is not None:
        row.monitored_ref = _monitored(session, body["monitored_ref"], roles_of(session, set_id))
    session.commit()
    return row


def archive_set(session: Session, set_id: str) -> DetectorSet:
    row = get_set(session, set_id)
    row.archived = True
    session.commit()
    return row


def referenced_by_send(session: Session, set_id: str) -> bool:
    """A set referenced by any send cannot be deleted; it can be archived (FR-009.13)."""
    return (
        session.execute(select(DetectorSend.id).where(DetectorSend.set_id == set_id)).first()
        is not None
    )


def list_sets(
    session: Session, *, page: int, limit: int, archived: bool | None
) -> tuple[list[DetectorSet], int]:
    query = select(DetectorSet)
    count = select(func.count()).select_from(DetectorSet)
    if archived is not None:
        query = query.where(DetectorSet.archived == archived)
        count = count.where(DetectorSet.archived == archived)
    rows = list(
        session.execute(
            query.order_by(DetectorSet.created_at.desc()).offset((page - 1) * limit).limit(limit)
        ).scalars()
    )
    return rows, int(session.execute(count).scalar_one())


# --- facts read from the version files --------------------------------------------------------


def _length_expr(column: str, arrow_type: pa.DataType, unit: str, columns: Sequence[str]) -> str:
    """A DuckDB expression for one row's length in ``unit`` (characters, or words)."""
    c = quote_ident(column, columns)

    def measure(text: str) -> str:
        if unit == "chars":
            return f"length({text})"
        return (
            f"CASE WHEN trim({text}) = '' THEN 0 "
            f"ELSE len(regexp_split_to_array(trim({text}), '\\s+')) END"
        )

    if pa.types.is_list(arrow_type) or pa.types.is_large_list(arrow_type):
        value_type = arrow_type.value_type
        if pa.types.is_struct(value_type):
            names = [value_type.field(i).name for i in range(value_type.num_fields)]
            part = "content" if "content" in names else "value" if "value" in names else None
            if part is not None:
                return (
                    f"list_sum(list_transform({c}, m -> {measure(f'CAST(m.{part} AS VARCHAR)')}))"
                )
    return measure(f"CAST({c} AS VARCHAR)")


def compute_lengths(path: str, column: str, unit: str) -> list[float]:
    schema = pq.read_schema(path)
    columns = schema.names
    expr = _length_expr(column, schema.field(column).type, unit, columns)
    con = connect()
    try:
        where = quote_ident(column, columns)
        rows = con.execute(
            f"SELECT {expr} AS n FROM read_parquet(?) WHERE {where} IS NOT NULL",  # noqa: S608
            [files_param([Path(path)])],
        ).fetchall()
    finally:
        con.close()
    return [float(r[0]) for r in rows if r[0] is not None]


def profile_for(
    session: Session, version: Version, split: str, column: str, unit: str = "chars"
) -> LengthProfile:
    """The cached length profile of (version, split, column, unit), computed once (FR-009.9)."""
    key = (version.id, split, column, unit, length.MEASURE_VERSION)
    row = session.get(LengthProfile, key)
    if row is not None:
        return row
    p = length.profile(compute_lengths(split_path(version, split), column, unit))
    session.execute(
        insert(LengthProfile)
        .values(
            version_id=version.id,
            split=split,
            column=column,
            unit=unit,
            measure_version=length.MEASURE_VERSION,
            n=p.n,
            quantiles=p.quantiles,
            histogram=p.histogram,
            permille=p.permille,
        )
        .on_conflict_do_nothing()
    )
    session.commit()
    found = session.get(LengthProfile, key)
    assert found is not None
    return found


def projected_label_counts(
    session: Session, version: Version, split: str, label_column: str
) -> dict[str, int]:
    """Label value -> rows, through 008's projection function (the one that writes the files).

    Null labels are counted under ``label_rules.NULL_KEY`` (``"None"``), the key miStudio maps them
    by: the rows ship in the published file, so D-2 must see them and the expected counts must
    include them (Humicroedit's middle band: 1,714 test rows were silently absent)."""
    path = split_path(version, split)
    counters = Counters()
    review = feature_seams.effective_labels_available()
    parquet = pq.ParquetFile(path)
    for batch in parquet.iter_batches(batch_size=65_536, columns=["_dw_row_key", label_column]):
        resolved = None
        if review:
            keys = batch.column("_dw_row_key").to_pylist()
            resolved = feature_seams.resolve_effective_labels(version.id, None, None, keys)
        apply_effective_labels(batch, label_column, resolved, counters)
    counts = dict(counters.label_counts)
    if counters.null_labels:
        counts[label_rules.NULL_KEY] = counts.get(label_rules.NULL_KEY, 0) + counters.null_labels
    return dict(sorted(counts.items()))


def synthetic_rows(version: Version, split: str) -> int:
    """Rows whose ``_dw_origin`` is not ``source`` (002 FR-002.23): D-6."""
    path = split_path(version, split)
    con = connect()
    try:
        n = con.execute(
            "SELECT count(*) FROM read_parquet(?) WHERE _dw_origin <> 'source'",
            [files_param([Path(path)])],
        ).fetchone()
    finally:
        con.close()
    return int(n[0]) if n else 0


# --- check inputs -----------------------------------------------------------------------------


def role_name(set_row: DetectorSet, role: DetectorSetRole, roles: Sequence[DetectorSetRole]) -> str:
    """The view name 009 registers for a role: also the key miStudio's threshold_transfer uses."""
    if role.display_name:
        return role.display_name
    words = ROLE_WORDS[role.role]
    if role.role == "ood_eval" and sum(1 for r in roles if r.role == "ood_eval") > 1:
        words = f"{words} {role.position + 1}"
    return f"{set_row.name} - {words}"


@dataclass
class Gathered:
    inputs: CheckInputs
    label_values: dict[str, dict[str, int]] = field(default_factory=dict)
    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    expected_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    monitored: dict[str, Any] | None = None
    #: The same profiles in words (FR-009.9); characters are the unit the check decides on.
    profiles_words: dict[str, dict[str, Any]] = field(default_factory=dict)


def _profile_out(row: LengthProfile) -> dict[str, Any]:
    return {
        "version_id": row.version_id,
        "split": row.split,
        "column": row.column,
        "unit": row.unit,
        "n": row.n,
        "quantiles": row.quantiles,
        "histogram": row.histogram,
        "finest_fpr": length.finest_fpr(row.n),
    }


def _labelers(
    session: Session, roles: Sequence[DetectorSetRole]
) -> tuple[list[LabelerStatus], list[str]]:
    """D-7: every labeler identity bound to a role's version, with 006's latest verdict."""
    by_fp: dict[str, dict[str, Any]] = {}
    no_record: list[str] = []
    for role in roles:
        version = session.get(Version, role.version_id)
        assert version is not None
        run_ids = [str(b["id"]) for b in version.bindings if b["kind"] == "label_run"]
        if not run_ids:
            no_record.append(role.id)
            continue
        found = feature_seams.labelers_for(run_ids, session=session)
        if found.status != feature_seams.CHECKED:
            by_fp.setdefault(
                "unknown",
                {"name": "labeler identities (005 absent)", "roles": [], "verdict": "none"},
            )["roles"].append(role.id)
            continue
        for lab in found.labelers:
            fp = str(lab["fingerprint"])
            entry = by_fp.setdefault(fp, {"name": str(lab["model_id"] or fp[:12]), "roles": []})
            entry["roles"].append(role.id)
    statuses: list[LabelerStatus] = []
    for fp, entry in sorted(by_fp.items()):
        if fp == "unknown":
            statuses.append(LabelerStatus(fp, entry["name"], tuple(entry["roles"]), "none"))
            continue
        cal = feature_seams.calibration_status(fp, session=session)
        verdict = str(cal.verdict) if cal.recorded and cal.verdict else "none"
        statuses.append(
            LabelerStatus(
                fp, entry["name"], tuple(entry["roles"]), verdict, cal.record_id, cal.auroc
            )
        )
    return statuses, no_record


def gather_check_inputs(session: Session, set_row: DetectorSet) -> Gathered:
    roles = roles_of(session, set_row.id)
    facts: list[RoleFacts] = []
    label_values: dict[str, dict[str, int]] = {}
    expected: dict[str, dict[str, int]] = {}
    profiles: dict[str, dict[str, Any]] = {}
    words: dict[str, dict[str, Any]] = {}
    lengths: dict[str, list[float]] = {}
    synthetic: dict[str, int] = {}
    versions: dict[str, Version] = {}
    for role in roles:
        version = session.get(Version, role.version_id)
        assert version is not None
        versions[role.id] = version
        name = role_name(set_row, role, roles)
        ok = version.state == VersionState.COMPLETED
        problems: tuple[label_rules.MappingProblem, ...] = ()
        counts: dict[str, int] = {}
        if ok:
            counts = projected_label_counts(session, version, role.split, role.label_column)
            problems = tuple(label_rules.check_mapping(role.role, counts, role.label_mapping))
            label_values[role.id] = counts
            expected[role.id] = label_rules.expected_counts(counts, role.label_mapping)
            prof = profile_for(session, version, role.split, role.input_column, "chars")
            profiles[role.id] = _profile_out(prof)
            words[role.id] = _profile_out(
                profile_for(session, version, role.split, role.input_column, "words")
            )
            lengths[role.id] = list(prof.permille)
            if role.role in ("id_test", "ood_eval"):
                synthetic[name] = synthetic_rows(version, role.split)
        facts.append(RoleFacts(role.id, role.role, name, ok, version.state, problems, counts))

    # the monitored reference
    monitored: dict[str, Any] | None = None
    ref = set_row.monitored_ref
    ref_lengths: list[float] | None = None
    if ref is not None and ref["kind"] == "role":
        if ref["role_id"] in lengths:
            ref_lengths = lengths[ref["role_id"]]
            monitored = {**ref, "profile": profiles[ref["role_id"]]}
    elif ref is not None and ref["kind"] == "version":
        version = _version(session, ref["version_id"])
        prof = profile_for(session, version, ref["split"], ref["column"], "chars")
        ref_lengths = list(prof.permille)
        monitored = {**ref, "profile": _profile_out(prof)}

    cal = next((r for r in roles if r.role == "calibration_negatives"), None)
    overlap = None
    if cal is not None and cal.id in lengths and ref_lengths:
        overlap = length.overlap(lengths[cal.id], ref_lengths)

    completed = [r for r in roles if versions[r.id].state == VersionState.COMPLETED]
    audited = [r for r in completed if r.role in ("train", "id_test")]
    train = next((r for r in completed if r.role == "train"), None)
    if audited:
        warnings = curation_seam.evaluate_warnings(
            [(r.version_id, r.split, r.role) for r in audited],
            label_column=train.label_column if train is not None else audited[0].label_column,
            session=session,
            label_sources=declared_label_sources(set_row, audited, roles),
        )
    else:
        warnings = checks.WarningFacts()
    # Each role is grouped by ITS OWN pair column; a role with none contributes no groups. One
    # set-wide column (the first role's) refused every set mixing paired and unpaired roles.
    paired = any(r.pair_column for r in completed)
    group_columns = {r.id: (r.pair_column or None) for r in completed} if paired else None
    if len(completed) >= 2:
        by_id = {r.id: role_name(set_row, r, roles) for r in completed}
        try:
            raw = curation_seam.check_leakage(
                [(r.version_id, r.split, r.id) for r in completed],
                group_columns=group_columns,
                session=session,
            )
        except CurationError as exc:
            role_id = (exc.details or {}).get("role")
            if exc.code != "group_column_missing" or role_id not in by_id:
                raise
            raise CurationError(
                exc.code,
                f"Role {by_id[role_id]} declares the pair column "
                f"{exc.details['group_column']!r}, which its version "
                f"{exc.details['version_id']} does not have.",
                {**exc.details, "role": by_id[role_id], "role_id": role_id},
            ) from None
        named: dict[str, int] = {}
        for key, n in raw.crossing.items():
            a, b = key.split("|")
            label = "|".join(sorted((by_id.get(a, a), by_id.get(b, b))))
            named[label] = named.get(label, 0) + n
        leakage = checks.LeakageFacts(crossing=named)
    else:
        leakage = checks.LeakageFacts()
    mined, elsewhere = _mined(session, set_row, roles, completed)
    if mined or elsewhere:
        leakage = checks.LeakageFacts(
            crossing=leakage.crossing, mined=mined, mined_elsewhere=elsewhere
        )
    labelers, no_record = _labelers(session, completed)
    inputs = CheckInputs(
        roles=facts,
        warnings=warnings,
        leakage=leakage,
        overlap=overlap,
        overlap_min=get_settings().detector_length_overlap_min,
        synthetic_rows=synthetic,
        labelers=labelers,
        no_record_needed=no_record,
        negatives_basis=cal.negatives_basis if cal is not None else None,
        monitored_ref_named=ref is not None,
    )
    return Gathered(inputs, label_values, profiles, expected, monitored, words)


def declared_label_sources(
    set_row: DetectorSet, audited: Sequence[DetectorSetRole], roles: Sequence[DetectorSetRole]
) -> dict[str, str]:
    """Column -> the roles that declared the label was computed from it (D-3 excludes it).

    The audit runs over the UNION of the audited roles, so a column declared by any of them is set
    aside, and the reason names every role that declared it."""
    by_column: dict[str, list[str]] = {}
    for r in audited:
        for column in r.label_source_columns or []:
            by_column.setdefault(str(column), []).append(role_name(set_row, r, roles))
    return {c: ", ".join(sorted(set(names))) for c, names in sorted(by_column.items())}


def run_checks(session: Session, set_id: str) -> dict[str, Any]:
    """``POST /detector-sets/{id}/checks``: D-1 to D-8, the values each mapping must cover, and
    both length profiles (FR-009.5, FR-009.9, FR-009.14)."""
    set_row = get_set(session, set_id)
    gathered = gather_check_inputs(session, set_row)
    outcomes = checks.evaluate(gathered.inputs)
    first = checks.first_refusal(outcomes)
    return {
        "set_id": set_id,
        "outcomes": [o.as_dict() for o in outcomes],
        "send_allowed": checks.send_allowed(outcomes),
        "first_refusal": first.as_dict() if first is not None else None,
        "label_values": gathered.label_values,
        "expected_counts": gathered.expected_counts,
        "profiles": gathered.profiles,
        "profiles_words": gathered.profiles_words,
        "monitored": gathered.monitored,
        "overlap_min": get_settings().detector_length_overlap_min,
    }


# --- read models ------------------------------------------------------------------------------


def role_out(
    set_row: DetectorSet, role: DetectorSetRole, roles: Sequence[DetectorSetRole], session: Session
) -> dict[str, Any]:
    version = session.get(Version, role.version_id)
    dataset = session.get(Dataset, version.dataset_id) if version is not None else None
    return {
        "id": role.id,
        "role": role.role,
        "role_words": ROLE_WORDS[role.role],
        "position": role.position,
        "version_id": role.version_id,
        "version_number": version.number if version is not None else None,
        "version_state": version.state if version is not None else None,
        "dataset_id": version.dataset_id if version is not None else None,
        "dataset_name": dataset.name if dataset is not None else None,
        "split": role.split,
        "input_column": role.input_column,
        "label_column": role.label_column,
        "label_mapping": role.label_mapping,
        "pair_column": role.pair_column,
        "label_source_columns": list(role.label_source_columns or []),
        "negatives_basis": role.negatives_basis,
        "display_name": role.display_name,
        "view_name": role_name(set_row, role, roles),
    }


def _mined(
    session: Session, set_row: DetectorSet, roles: Sequence[Any], completed: Sequence[Any]
) -> tuple[dict[str, str], list[str]]:
    """FR-009.59: a role whose version was mined from an evaluation role of THIS set overlaps it
    (D-4 refuses); one mined from another set's evaluation data is named in D-4's details."""
    from .mining import mined_from_evaluation

    by_role_id = {r.id: role_name(set_row, r, roles) for r in roles}
    mined: dict[str, str] = {}
    elsewhere: list[str] = []
    for r in completed:
        for source in mined_from_evaluation(session, r.version_id):
            if source.set_id == set_row.id and source.role_id != r.id:
                mined[by_role_id[r.id]] = by_role_id[source.role_id]
            elif source.set_id != set_row.id:
                elsewhere.append(f"{by_role_id[r.id]}: {source.set_name} / {source.role}")
    return mined, sorted(set(elsewhere))


def set_summary(session: Session, row: DetectorSet) -> dict[str, Any]:
    roles = roles_of(session, row.id)
    last_send = session.execute(
        select(DetectorSend)
        .where(DetectorSend.set_id == row.id)
        .order_by(DetectorSend.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    last_results = session.execute(
        select(DetectorResults)
        .where(DetectorResults.set_id == row.id)
        .order_by(DetectorResults.read_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    rung = None
    if last_results is not None:
        selected = [f for f in last_results.figures if f.get("selected") and not f.get("reward")]
        if selected:
            rung = {"rung": selected[0]["rung"], "rung_language": selected[0]["rung_language"]}
    counts: dict[str, int] = {}
    for r in roles:
        counts[r.role] = counts.get(r.role, 0) + 1
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "positive_meaning": row.positive_meaning,
        "archived": row.archived,
        "role_counts": counts,
        "last_send_state": last_send.state if last_send is not None else None,
        "last_send_id": last_send.id if last_send is not None else None,
        "last_rung": rung,
        "created_by": row.created_by,
        "created_by_origin": row.created_by_origin,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def set_out(session: Session, row: DetectorSet) -> dict[str, Any]:
    roles = roles_of(session, row.id)
    profiles: dict[str, Any] = {}
    for r in roles:
        prof = session.get(
            LengthProfile, (r.version_id, r.split, r.input_column, "chars", length.MEASURE_VERSION)
        )
        if prof is not None:
            profiles[r.id] = _profile_out(prof)
    return {
        **set_summary(session, row),
        "monitored_ref": row.monitored_ref,
        "roles": [role_out(row, r, roles, session) for r in roles],
        "profiles": profiles,
    }
