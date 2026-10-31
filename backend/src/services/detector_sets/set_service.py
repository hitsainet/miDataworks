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
from ...models.text_separability import TextSeparability
from ...models.version import Version
from ..curation.audit_service import AUDITED_SYSTEM
from ..curation.errors import CurationError
from ..curation.held_out_files import facts as held_out_facts
from ..curation.held_out_proof import DirectedBy, prove
from ..duck import connect, files_param, quote_ident
from ..publishing import feature_seams
from ..publishing.projection import Counters, apply_effective_labels, label_text
from . import checks, curation_seam, label_rules, length, separation_wording
from .checks import CheckInputs, LabelerStatus, RoleFacts
from .errors import DetectorSetError
from .plan import mapping_sha256
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
#: 017 FR-017.33: an assumed-negative basis's evidence; omitted from the record when absent.
_EVIDENCE_FIELDS = ("screen", "second_opinion")


def _basis_record(basis: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not basis:
        return None
    record = dict(basis)
    if record.get("kind") == "human_labelled":
        return {k: v for k, v in record.items() if v is not None}
    return {
        k: v
        for k, v in record.items()
        if not (k in (*_HUMAN_FIELDS, *_EVIDENCE_FIELDS) and v is None)
    }


def _validate_behaviour_basis(
    session: Session, version: Version, spec: Mapping[str, Any], behaviour_key: str | None
) -> None:
    """017 FR-017.33: a behaviour's assumed negatives name their evidence. A ``screen`` must have
    run (``use = screen``, the exact pinned version) in the build of the role's version; a
    ``second_opinion`` sample holds at least ``SECOND_OPINION_NO_RULE_NEGATIVES`` rows; a basis
    with neither is refused. Sets without a behaviour key keep 009's rule (assumed negative alone).
    """
    basis = spec.get("negatives_basis") or {}
    if not behaviour_key or spec["role"] != "calibration_negatives":
        return
    if basis.get("kind") != "assumed_negative":
        return
    screen, second = basis.get("screen") or [], basis.get("second_opinion")
    if not screen and not second:
        raise DetectorSetError(
            "negatives_basis_incomplete",
            f"Behaviour {behaviour_key!r}'s calibration negatives are assumed negative with no "
            "evidence: name the screen (the rule sets whose positive signals screened the rows) "
            "or, for a behaviour with no rule signal, the second-opinion sample and its estimated "
            "positive rate (FR-017.33).",
            {
                "role": spec["role"],
                "next": {
                    "tool": "dataworks_list_rule_sets",
                    "args": {},
                    "why": "find the screening rule sets the version's build ran",
                },
            },
        )
    if second:
        floor = int(get_settings().second_opinion_no_rule_negatives)
        if int(second["sample_rows"]) < floor:
            raise DetectorSetError(
                "negatives_basis_incomplete",
                f"The second-opinion sample behind these negatives holds {second['sample_rows']} "
                f"rows; SECOND_OPINION_NO_RULE_NEGATIVES asks for at least {floor}.",
                {
                    "role": spec["role"],
                    "setting": "SECOND_OPINION_NO_RULE_NEGATIVES",
                    "limit": floor,
                    "actual": int(second["sample_rows"]),
                    "next": {"tool": "dataworks_get_settings", "args": {}, "why": "read the floor"},
                },
            )
    if screen:
        from ..rules.carry import carried

        ran = {
            (c.name, c.version, c.sha256): c
            for c in (carried(session, version) or [])
            if c.use == "screen"
        }
        missing = [p for p in screen if (p["name"], int(p["version"]), p["sha256"]) not in ran]
        if missing:
            first = missing[0]
            raise DetectorSetError(
                "negatives_basis_unproven",
                f"The basis names screen {first['name']}@{first['version']}, but version "
                f"{version.number}'s build ran no rule_set@1 step with use = screen on that exact "
                "version: the negatives were not screened by it.",
                {
                    "role": spec["role"],
                    "missing": [f"{p['name']}@{p['version']}" for p in missing],
                    "next": {
                        "tool": "dataworks_get_version",
                        "args": {"version_id": version.id},
                        "why": "see the rule sets the version's build ran (rule_sets)",
                    },
                },
            )
        for p in screen:
            item = ran[(p["name"], int(p["version"]), p["sha256"])]
            why = _screen_not_applied(session, version, item, str(spec["input_column"]))
            if why is not None:
                raise DetectorSetError(
                    "negatives_basis_unproven",
                    f"The basis names screen {p['name']}@{p['version']}, which ran in version "
                    f"{version.number}'s build, but {why}: the role's negatives were not "
                    "screened by it.",
                    {
                        "role": spec["role"],
                        "missing": [f"{p['name']}@{p['version']}"],
                        "next": {
                            "tool": "dataworks_get_version",
                            "args": {"version_id": version.id},
                            "why": "see the version's steps and the rule sets its build ran",
                        },
                    },
                )


def _screen_not_applied(
    session: Session, version: Version, item: Any, input_column: str
) -> str | None:
    """Review R3: a screen that RAN is not yet a screen that removed anything. Proven only when,
    in the version's own build, a ``rule_set@1`` step pinned on that exact rule set ran with
    ``use = screen`` over the role's input column, a later ``screen_filter@1`` drops on EVERY
    boolean output that step wrote (with its prefix), and no step after the screen changed or
    added a row (a changed text was not screened; an added row never was). None when proven, else
    why not (never row text)."""
    import json

    from ...models.recipe import RecipeBody
    from ...models.step_execution import StepExecution
    from ...models.version import VersionStep
    from ..rules.step_resolution import pin_of

    body = session.get(RecipeBody, version.recipe_hash)
    steps: list[dict[str, Any]] = list((body.body if body is not None else {}).get("steps") or [])
    outputs = json.loads(item.body_canonical).get("outputs") or []
    flags = [str(o["column"]) for o in outputs if o.get("type") == "bool"]
    executions = {
        int(index): execution
        for index, execution in session.execute(
            select(VersionStep.step_index, StepExecution)
            .join(StepExecution, StepExecution.id == VersionStep.step_execution_id)
            .where(VersionStep.version_id == version.id)
        ).all()
    }
    why = "no rule_set@1 step of its recipe ran it with use = screen"
    for index, step in enumerate(steps, start=1):
        params = step.get("params") or {}
        if step.get("operator") != "rule_set" or params.get("use") != "screen":
            continue
        if pin_of(params) != (item.name, item.version, item.sha256):
            continue
        if str(params.get("text_column")) != input_column:
            why = (
                f"step {index} screened column {params.get('text_column')!r}, not the role's "
                f"input column {input_column!r}"
            )
            continue
        prefix = params.get("output_prefix")
        prefix = "rs_" if prefix is None else str(prefix)
        required = {prefix + c for c in flags}
        filtered = any(
            later.get("operator") == "screen_filter"
            and required <= set((later.get("params") or {}).get("flag_columns") or [])
            for later in steps[index:]
        )
        if not required or not filtered:
            why = (
                f"no screen_filter@1 after step {index} drops on all of its flags "
                f"{sorted(required)}"
            )
            continue
        touched = [
            i
            for i, execution in sorted(executions.items())
            if i > index
            and (execution.rows_changed is None or execution.rows_changed > 0
                 or execution.rows_added is None or execution.rows_added > 0)
        ]  # fmt: skip
        if touched:
            why = f"step {touched[0]} changed or added rows after the screen (step {index})"
            continue
        return None
    return why


def _insert_roles(
    session: Session, set_row: DetectorSet, roles: Sequence[Mapping[str, Any]]
) -> list[DetectorSetRole]:
    rows: list[DetectorSetRole] = []
    ood = 0
    for spec in roles:
        version = _validate_role(session, spec)
        _validate_behaviour_basis(session, version, spec, set_row.behaviour_key)
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


def recorded_definition(
    session: Session,
    row: DetectorSet,
    explicit: Mapping[str, Any] | None,
    roles: Sequence[DetectorSetRole],
) -> dict[str, Any] | None:
    """017 FR-017.11: the definition a set records. One supplied (the resolve answer's
    ``record``) must name the set's key; else the training role's version's recorded definition
    when it names the same key (or the set has none). Never a guess: otherwise None."""
    if explicit is not None:
        if row.behaviour_key and explicit.get("key") != row.behaviour_key:
            raise DetectorSetError(
                "behaviour_definition_mismatch",
                f"The definition names behaviour {explicit.get('key')!r}; this set's key is "
                f"{row.behaviour_key!r}. Record the set's own behaviour's definition.",
                {
                    "definition_key": explicit.get("key"),
                    "behaviour_key": row.behaviour_key,
                    "next": {
                        "tool": "dataworks_resolve_behaviour",
                        "args": {"behaviour_key": row.behaviour_key},
                        "why": "read the definition of the set's own behaviour",
                    },
                },
            )
        return dict(explicit)
    train = next((r for r in roles if r.role == "train"), None)
    version = session.get(Version, train.version_id) if train is not None else None
    found = version.behaviour_definition if version is not None else None
    if isinstance(found, dict) and row.behaviour_key in (None, found.get("key")):
        return dict(found)
    return None


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
        behaviour_key=body.get("behaviour_key") or None,
        archived=False,
        created_by=who.who,
        created_by_origin=who.origin,
    )
    session.add(row)
    session.flush()
    roles = _insert_roles(session, row, body.get("roles") or [])
    row.monitored_ref = _monitored(session, body.get("monitored_ref"), roles)
    row.behaviour_definition = recorded_definition(
        session, row, body.get("behaviour_definition"), roles
    )
    row.benchmark_source_ids = benchmark_sources(session, body.get("benchmark_source_ids") or [])
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
    before = row.behaviour_key
    if body.get("behaviour_key") is not None:
        # 015: "" clears the key; absent or null leaves it.
        row.behaviour_key = body["behaviour_key"] or None
    if body.get("roles") is None and row.behaviour_key and row.behaviour_key != before:
        # Review R3: a key given (or changed) later binds the roles already saved: their
        # calibration negatives must name the evidence 017 asks of a behaviour's set (FR-017.33).
        for kept in roles_of(session, set_id):
            _validate_behaviour_basis(
                session,
                _version(session, kept.version_id),
                {
                    "role": kept.role,
                    "input_column": kept.input_column,
                    "negatives_basis": kept.negatives_basis,
                },
                row.behaviour_key,
            )
    if body.get("roles") is not None:
        for old in roles_of(session, set_id):
            session.delete(old)
        session.flush()
        roles = _insert_roles(session, row, body["roles"])
        row.monitored_ref = _monitored(session, body.get("monitored_ref"), roles)
    elif "monitored_ref" in body and body["monitored_ref"] is not None:
        row.monitored_ref = _monitored(session, body["monitored_ref"], roles_of(session, set_id))
    current = row.behaviour_definition
    if body.get("behaviour_definition") is not None:
        row.behaviour_definition = recorded_definition(
            session, row, body["behaviour_definition"], roles_of(session, set_id)
        )
    elif current is None or (row.behaviour_key and current.get("key") != row.behaviour_key):
        # 017: none recorded yet, or the set's key moved away from it: take the training version's
        row.behaviour_definition = recorded_definition(
            session, row, None, roles_of(session, set_id)
        )
    if body.get("benchmark_source_ids") is not None:
        row.benchmark_source_ids = benchmark_sources(session, body["benchmark_source_ids"])
    session.commit()
    return row


def benchmark_sources(session: Session, ids: Sequence[str]) -> list[str]:
    """017 TD-017.30: the named benchmarks, each an existing source, unique and sorted. A source
    need not be imported yet at a pinned revision (the check refuses that when it runs)."""
    import uuid as _uuid

    from ...models.source import Source

    out = sorted(dict.fromkeys(str(i) for i in ids))
    for source_id in out:
        try:
            _uuid.UUID(source_id)
            found = session.get(Source, source_id)
        except ValueError:  # not a source id at all: the same answer as an unknown one
            found = None
        if found is None:
            raise DetectorSetError(
                "benchmark_not_found",
                f"Benchmark {source_id} is not a source of this deployment; import it first.",
                {
                    "benchmark_source_id": source_id,
                    "next": {
                        "tool": "dataworks_import_hf_dataset",
                        "args": {},
                        "why": "import the benchmark at the revision to pin",
                    },
                },
            )
    return out


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
    session: Session,
    *,
    page: int,
    limit: int,
    archived: bool | None,
    behaviour_key: str | None = None,
) -> tuple[list[DetectorSet], int]:
    query = select(DetectorSet)
    count = select(func.count()).select_from(DetectorSet)
    if behaviour_key:  # 017 FR-017.53, answered from the behaviour_key index (0035)
        query = query.where(DetectorSet.behaviour_key == behaviour_key)
        count = count.where(DetectorSet.behaviour_key == behaviour_key)
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


# --- D-9 "wording alone" (015 FR-015.15 - FR-015.17) -------------------------------------------


def _sample_rows(
    version: Version,
    role: DetectorSetRole,
    sample_n: int,
    seed: int,
    group_column: str | None = None,
) -> tuple[list[str], list[int], list[str] | None]:
    """A seeded, label-stratified subsample of at most ``sample_n`` rows (texts, 1/0, groups).

    Rows are ordered by ``hash(row key, seed)`` in DuckDB, so a re-run takes the same rows; each
    class keeps its share of ``sample_n``. Unmapped and ``excluded`` labels are left out, as miStudio
    leaves them out of training. Text stays in memory and is never logged."""
    path = split_path(version, role.split)
    schema = pq.read_schema(path)
    names = schema.names
    label = quote_ident(role.label_column, names)
    quote_ident(role.input_column, names)  # refuses a column the split does not have
    group_name = group_column or role.pair_column
    pair = quote_ident(group_name, names) if group_name else None
    con = connect()
    try:
        keyed = con.execute(
            f"SELECT _dw_row_key, {label} FROM read_parquet(?) "  # noqa: S608
            "ORDER BY hash(_dw_row_key || ?), _dw_row_key",
            [files_param([Path(path)]), str(seed)],
        ).fetchall()
    finally:
        con.close()
    by_class: dict[int, list[str]] = {1: [], 0: []}
    for key, value in keyed:
        target = label_rules.target_for(
            label_rules.NULL_KEY if value is None else label_text(value), role.label_mapping
        )
        if target == "positive":
            by_class[1].append(str(key))
        elif target == "negative":
            by_class[0].append(str(key))
    total = len(by_class[1]) + len(by_class[0])
    if total > sample_n:
        take_pos = round(sample_n * len(by_class[1]) / total)
        chosen = {1: by_class[1][:take_pos], 0: by_class[0][: sample_n - take_pos]}
    else:
        chosen = by_class
    wanted = {k: y for y, keys in chosen.items() for k in keys}
    columns = ["_dw_row_key", role.input_column] + ([group_name] if pair and group_name else [])
    table = pq.read_table(path, columns=columns)
    texts: list[str] = []
    labels: list[int] = []
    groups: list[str] | None = [] if pair else None
    keys = table.column("_dw_row_key").to_pylist()
    values = table.column(role.input_column).to_pylist()
    pairs = table.column(group_name).to_pylist() if pair and group_name else None
    for i, key in enumerate(keys):
        y = wanted.get(str(key))
        if y is None:
            continue
        texts.append(separation_wording.as_text(values[i]))
        labels.append(y)
        if groups is not None and pairs is not None:
            groups.append("" if pairs[i] is None else str(pairs[i]))
    return texts, labels, groups


def wording_for(
    session: Session,
    version: Version,
    role: DetectorSetRole,
    sample_n: int,
    *,
    by_value: str | None = None,
) -> TextSeparability:
    """The cached D-9 figure of (version, split, columns, mapping, pair column, n), computed once.

    Keyed like ``profile_for``: a check page never refits (FR-015.17). ``by_value`` (017
    FR-017.45, OQ-017-3 (a)): the leave-one-value-out figure over that column instead, cached
    under its own measure with the column in the pair-column slot."""
    key = (
        version.id,
        role.split,
        role.input_column,
        role.label_column,
        mapping_sha256(role.label_mapping),
        by_value if by_value is not None else (role.pair_column or ""),
        int(sample_n),
        (
            separation_wording.GROUPED_MEASURE_VERSION
            if by_value is not None
            else separation_wording.MEASURE_VERSION
        ),
    )
    row = session.get(TextSeparability, key)
    if row is not None:
        return row
    texts, labels, groups = _sample_rows(
        version, role, sample_n, separation_wording.SEED, group_column=by_value
    )
    result = separation_wording.wording_auroc(
        texts, labels, groups, kind="leave_one_value_out" if by_value is not None else None
    )
    session.execute(
        insert(TextSeparability)
        .values(
            version_id=key[0],
            split=key[1],
            input_column=key[2],
            label_column=key[3],
            mapping_sha256=key[4],
            pair_column=key[5],
            sample_n=key[6],
            measure_version=key[7],
            n=result.n,
            n_positive=result.n_positive,
            n_negative=result.n_negative,
            auroc=result.auroc,
            folds=result.folds,
            grouped=result.grouped,
            seed=separation_wording.SEED,
            reason=result.reason,
        )
        .on_conflict_do_nothing()
    )
    session.commit()
    logger.info(
        "detector_set.wording version=%s split=%s n=%d auroc=%s reason=%s",
        version.id,
        role.split,
        result.n,
        result.auroc,
        result.reason,
    )
    found = session.get(TextSeparability, key)
    assert found is not None
    return found


def _wording_facts(
    session: Session,
    set_row: DetectorSet,
    roles: Sequence[DetectorSetRole],
    completed: Sequence[DetectorSetRole],
    versions: Mapping[str, Version],
) -> list[checks.WordingFacts]:
    """D-9 inputs for the training and in-distribution test roles, each compared at equal size
    with every computable out-of-distribution role (the reference, D55; FTID 015 I5)."""
    cap = int(get_settings().detector_text_separability_rows)
    measured = [r for r in completed if r.role in ("train", "id_test")]
    refs = [r for r in completed if r.role == "ood_eval"]
    ref_rows = {r.id: wording_for(session, versions[r.id], r, cap) for r in refs}
    out: list[checks.WordingFacts] = []
    for role in measured:
        own = wording_for(session, versions[role.id], role, cap)
        comparisons: list[checks.WordingComparison] = []
        if own.auroc is not None:
            for ref in refs:
                ref_own = ref_rows[ref.id]
                if ref_own.auroc is None:
                    continue
                n_eq = min(own.n, ref_own.n)
                role_eq = (
                    own if own.n == n_eq else wording_for(session, versions[role.id], role, n_eq)
                )
                ref_eq = (
                    ref_own
                    if ref_own.n == n_eq
                    else wording_for(session, versions[ref.id], ref, n_eq)
                )
                if role_eq.auroc is None or ref_eq.auroc is None:
                    continue
                comparisons.append(
                    checks.WordingComparison(
                        reference=role_name(set_row, ref, roles),
                        n=min(role_eq.n, ref_eq.n),
                        auroc=float(role_eq.auroc),
                        reference_auroc=float(ref_eq.auroc),
                    )
                )
        by_genre = None
        if GROUP_FOLDS_COLUMN in pq.read_schema(split_path(versions[role.id], role.split)).names:
            loo = wording_for(session, versions[role.id], role, cap, by_value=GROUP_FOLDS_COLUMN)
            by_genre = {
                "by": GROUP_FOLDS_COLUMN,
                "auroc": loo.auroc,
                "values": loo.folds,
                "n": loo.n,
                "reason": loo.reason,
            }
        out.append(
            checks.WordingFacts(
                role=role.role,
                name=role_name(set_row, role, roles),
                n=own.n,
                auroc=own.auroc,
                grouped=own.grouped,
                reason=own.reason,
                comparisons=tuple(comparisons),
                held_out_by_value=by_genre,
            )
        )
    return out


#: 017 OQ-017-3 (a): D-9 reports the figure with each genre held out beside its own when the role
#: carries this column (the FPRD's column name; values are behaviour data). D55's thresholds read
#: the role's own figure, unchanged.
GROUP_FOLDS_COLUMN = "genre"


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
    proven: dict[str, dict[str, Any]] = {}
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
                proof = _split(version, role.split).get("proof")
                if isinstance(proof, dict) and proof.get("passes"):
                    proven[name] = {**proof, "version_id": str(version.id)}  # D-6 (FR-017.4)
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
            label_sources=declared_label_sources(set_row, completed, roles),
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
    wording = _wording_facts(session, set_row, roles, completed, versions)
    cross = cross_version_facts(session, set_row, roles, completed, versions)
    evidence = evidence_facts(session, set_row, roles, completed, versions, expected, synthetic)
    second = second_opinion_facts(session, set_row, completed, versions)
    definition = definition_facts(session, set_row, completed, versions)
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
        wording=wording,
        proven_splits=proven,
        cross_version=cross,
        evidence=evidence,
        second_opinion=second,
        definition=definition,
    )
    return Gathered(inputs, label_values, profiles, expected, monitored, words)


# --- D-10 (017 FR-017.49, FR-017.11, FR-017.7) --------------------------------------------------

#: How far up a version's parents D-10 looks for "measured elsewhere" (it is read, never trusted).
ANCESTORS_READ = 16


def _ancestors(session: Session, version: Version) -> list[Version]:
    out: list[Version] = []
    current = version
    while current.parent_version_id is not None and len(out) < ANCESTORS_READ:
        parent = session.get(Version, current.parent_version_id)
        if parent is None:
            break
        out.append(parent)
        current = parent
    return out


def _newest_diversity(session: Session, version_id: str) -> Any:
    from ...models.generation import DiversityReport

    return session.execute(
        select(DiversityReport)
        .where(DiversityReport.version_id == version_id)
        .order_by(DiversityReport.created_at.desc(), DiversityReport.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _newest_contamination(session: Session, version_id: str) -> Any:
    from ...models.curation import VersionReport

    return session.execute(
        select(VersionReport)
        .where(
            VersionReport.version_id == version_id,
            VersionReport.kind == "contamination",
            VersionReport.state == "completed",
        )
        .order_by(VersionReport.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def evidence_facts(
    session: Session,
    set_row: DetectorSet,
    roles: Sequence[DetectorSetRole],
    completed: Sequence[DetectorSetRole],
    versions: Mapping[str, Version],
    expected: Mapping[str, Mapping[str, int]],
    synthetic: Mapping[str, int],
) -> list[checks.EvidenceFacts]:
    """D-10's per-role facts (TD-017.27): a result counts only on the role's exact version."""
    out: list[checks.EvidenceFacts] = []
    for role in completed:
        version = versions[role.id]
        name = role_name(set_row, role, roles)
        div = _newest_diversity(session, version.id)
        con = _newest_contamination(session, version.id)
        div_elsewhere = con_elsewhere = None
        if div is None or con is None:
            for parent in _ancestors(session, version):
                if div is None and div_elsewhere is None and _newest_diversity(session, parent.id):
                    div_elsewhere = _version_label(session, parent)
                if (
                    con is None
                    and con_elsewhere is None
                    and _newest_contamination(session, parent.id)
                ):
                    con_elsewhere = _version_label(session, parent)
        contamination = None
        if con is not None:
            benches = list((con.result or {}).get("benchmarks") or [])
            contamination = {
                "report_id": str(con.id),
                "rows_overlapping": sum(int(b.get("rows_overlapping") or 0) for b in benches),
                "benchmarks": [b.get("source_id") for b in benches],
            }
        mixed = None
        if role.role in checks.EVALUATION_ROLES:
            rows = int(_split(version, role.split).get("rows") or 0)
            generated = int(synthetic.get(name, 0))
            if 0 < generated < rows:
                mixed = (generated, rows)
        planned = (version.behaviour_configuration or {}).get("held_out_rows")
        out.append(
            checks.EvidenceFacts(
                role=name,
                kind=role.role,
                version_id=str(version.id),
                version_label=_version_label(session, version),
                diversity=(
                    {"verdict": div.verdict, "report_id": div.id} if div is not None else None
                ),
                diversity_elsewhere=div_elsewhere,
                contamination=contamination,
                contamination_elsewhere=con_elsewhere,
                counts_by_class={
                    k: int(v)
                    for k, v in (expected.get(role.id) or {}).items()
                    if k in ("positive", "negative")
                },
                planned_rows=int(planned) if isinstance(planned, int) else None,
                mixed_origin=mixed,
            )
        )
    return out


def second_opinion_facts(
    session: Session,
    set_row: DetectorSet,
    completed: Sequence[DetectorSetRole],
    versions: Mapping[str, Version],
) -> checks.SecondOpinionFacts | None:
    """The hand-off runs for the set's behaviour, and whether the training version's build ran a
    rule set (016's record). None for a set that names no behaviour: a second opinion is a
    behaviour dataset's (FR-017.38), and D-10 then says nothing of it."""
    from ...models.label_run import LabelRun
    from ..rules import carry

    if not set_row.behaviour_key:
        return None
    query = select(LabelRun.id, LabelRun.target_column, LabelRun.state, LabelRun.stop_reason).where(
        LabelRun.handoff_id.is_not(None), LabelRun.behaviour_key == set_row.behaviour_key
    )
    runs = [
        {"run_id": r[0], "target_column": r[1], "state": r[2], "stop_reason": r[3]}
        for r in session.execute(query.order_by(LabelRun.created_at, LabelRun.id)).all()
    ]
    train = next((r for r in completed if r.role == "train"), None)
    signal: bool | None = None
    if train is not None:
        used = carry.carried(session, versions[train.id])
        signal = None if used is None else bool(used)
    return checks.SecondOpinionFacts(runs=runs, rule_signal=signal)


def definition_facts(
    session: Session,
    set_row: DetectorSet,
    completed: Sequence[DetectorSetRole],
    versions: Mapping[str, Version],
) -> checks.DefinitionFacts | None:
    """FR-017.11: the set's and its versions' recorded definitions against miStudio's catalogue
    NOW (runtime-detected; unconfigured or unreachable reads "not verified")."""
    if not set_row.behaviour_key:
        return None
    from ..behaviour import resolve_service

    recorded = set_row.behaviour_definition or {}
    built: dict[str, int | None] = {}
    for role in completed:
        version = versions[role.id]
        found = version.behaviour_definition or {}
        if found.get("key") == set_row.behaviour_key:
            value = found.get("definition_version")
            built[_version_label(session, version)] = value if isinstance(value, int) else None
    answer = resolve_service.resolve(set_row.behaviour_key)
    definition = answer.get("definition") or {}
    current = answer.get("current_version")
    if current is None:
        current = definition.get("version")
    return checks.DefinitionFacts(
        key=set_row.behaviour_key,
        recorded_version=(
            recorded.get("definition_version")
            if isinstance(recorded.get("definition_version"), int)
            else None
        ),
        recorded_meaning=recorded.get("positive_meaning"),
        set_meaning=set_row.positive_meaning,
        built_against=built,
        verified=bool(answer.get("verified")),
        reason=answer.get("reason"),
        current_version=int(current) if isinstance(current, int) else None,
        archived=answer.get("archived"),
        catalogue_meaning=definition.get("positive_meaning"),
    )


def _version_label(session: Session, version: Version) -> str:
    dataset = session.get(Dataset, version.dataset_id)
    return f"{dataset.name if dataset else version.dataset_id} v{version.number}"


def cross_version_facts(
    session: Session,
    set_row: DetectorSet,
    roles: Sequence[DetectorSetRole],
    completed: Sequence[DetectorSetRole],
    versions: Mapping[str, Version],
) -> list[checks.CrossVersionFacts]:
    """D-11 (017 FR-017.8; ENH-006 D42, D45): each kept role bound to ANOTHER version than the
    training role's, proved against the training role's rows. Every such role (evaluation roles
    since D45, calibration negatives since D42), value-directed or not: no row of it may be a
    training row (row keys), so a kept row copied verbatim into new training refuses. When its
    split is value-directed, also: its held-out values and groups must be absent from them. A role
    on the training version itself is covered by that version's own proof."""
    train = next((r for r in completed if r.role == "train"), None)
    if train is None:
        return []
    train_version = versions[train.id]
    train_path = Path(split_path(train_version, train.split))
    train_keys: set[str] | None = None
    out: list[checks.CrossVersionFacts] = []
    for role in completed:
        if role.role not in CROSS_VERSION_ROLES or role.version_id == train.version_id:
            continue
        version = versions[role.id]
        entry = _split(version, role.split)
        kept_path = Path(split_path(version, role.split))
        directed_split = bool(entry.get("held_out") and entry.get("directed_by"))
        if train_keys is None:
            train_keys = _row_keys(train_path)
        shared_rows = len(_row_keys(kept_path) & train_keys)
        columns: tuple[str, ...] = ()
        intersection: tuple[tuple[str, str, int], ...] = ()
        shared_groups: dict[str, int] = {}
        missing: tuple[str, ...] = ()
        if directed_split:
            directed = DirectedBy.from_dict(entry["directed_by"])
            needed = (*directed.columns, *directed.group_columns)
            present = set(pq.read_schema(train_path).names) & set(pq.read_schema(kept_path).names)
            missing = tuple(c for c in needed if c not in present)
            found = held_out_facts(directed, [kept_path], {train.split: train_path})
            proof = prove(role.split, directed, found)
            columns = directed.columns
            intersection = proof.intersection
            shared_groups = dict(proof.shared_groups)
        out.append(
            checks.CrossVersionFacts(
                role=role_name(set_row, role, roles),
                version_id=str(version.id),
                version_label=_version_label(session, version),
                training_role=role_name(set_row, train, roles),
                training_version_id=str(train_version.id),
                training_version_label=_version_label(session, train_version),
                columns=columns,
                intersection=intersection,
                shared_groups=shared_groups,
                missing_columns=missing,
                shared_rows=shared_rows,
                kind=role.role,
            )
        )
    return out


#: D-11's kept roles: the two evaluation roles (FR-017.8; row keys since D45) and calibration
#: negatives (D42).
CROSS_VERSION_ROLES: tuple[str, ...] = ("id_test", "ood_eval", "calibration_negatives")


def _row_keys(path: Path) -> set[str]:
    return {
        str(k)
        for k in pq.read_table(path, columns=["_dw_row_key"]).column(0).to_pylist()
        if k is not None
    }


def declared_label_sources(
    set_row: DetectorSet, audited: Sequence[DetectorSetRole], roles: Sequence[DetectorSetRole]
) -> dict[str, str]:
    """Column -> the roles that declared the label was computed from it (D-3 excludes it).

    The audit runs over the UNION of the audited roles (train, id_test). Every role of the set
    shares one label definition, so a column ANY completed role declares - calibration_negatives
    and ood_eval included - is set aside wherever it appears, and the reason names every role that
    declared it. (e2e run G8, 2026-10-21: only train and id_test declarations were read, so a
    declaration on the calibration role was silently ignored.)"""
    by_column: dict[str, list[str]] = {}
    for r in audited:
        for column in r.label_source_columns or []:
            by_column.setdefault(str(column), []).append(role_name(set_row, r, roles))
    return {c: ", ".join(sorted(set(names))) for c, names in sorted(by_column.items())}


def run_checks(session: Session, set_id: str) -> dict[str, Any]:
    """``POST /detector-sets/{id}/checks``: D-1 to D-11, the values each mapping must cover, and
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
        "behaviour_key": row.behaviour_key,
        "behaviour_definition": row.behaviour_definition,
        "benchmark_source_ids": list(row.benchmark_source_ids or []),
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
