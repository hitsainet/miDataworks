"""Input assembly: step 0 of every build (FR-002.7, 002.8, 002.20, 002.21, 002.23; FTID 002 §7.1).

Reads the build's inputs in request order — a source contributes its files' rows by split, a version
contributes its split files — adds the reserved system columns, computes every row key with the
initial column roles, assigns occurrence numbers in output order, and writes one Parquet input set
in ``PARQUET_ROW_GROUP_ROWS`` row groups. Nothing is held whole in memory: inputs stream in record
batches and the writer appends.

Refusals: a user column starting with ``_dw_`` (``reserved_column``); two inputs with a same-named
column of different types (``input_schema_conflict``); a version with no content column
(``no_content_columns``); a file whose bytes differ from the hash recorded at import
(``source_hash_mismatch``, checked before reading, FR-002.7).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from ..core.config import get_settings
from ..core.errors import AppError, NotFoundError
from ..models.enums import ColumnRole, Origin, TargetType
from .identity import file_sha256, logical_digest
from .row_keys import compute_row_key
from .step_contract import RESERVED_PREFIX, SYSTEM_COLUMNS

logger = logging.getLogger(__name__)

#: Default content columns per target type (FR-002.20). ``sft`` takes ``messages`` when present,
#: otherwise ``text``; ``detector`` and ``untyped`` take feature 001's detected text column.
DEFAULT_CONTENT_COLUMNS: dict[str, tuple[str, ...]] = {
    TargetType.SFT: ("messages", "text"),
    TargetType.DPO: ("prompt", "chosen", "rejected"),
    TargetType.KTO: ("prompt", "completion"),
    TargetType.GRPO_PROMPT: ("prompt",),
    TargetType.PRM: ("prompt", "completions"),
    TargetType.DETECTOR: (),
    TargetType.UNTYPED: (),
}


class BuildRefusal(AppError):
    """A refusal raised while a build runs; the job fails with this envelope."""

    status_code = 409


def default_content_columns(
    target_type: str, columns: Sequence[str], detected_text: Sequence[str]
) -> list[str]:
    present = set(columns)
    if target_type == TargetType.SFT:
        for candidate in DEFAULT_CONTENT_COLUMNS[TargetType.SFT]:
            if candidate in present:
                return [candidate]
        return []
    if target_type in (TargetType.DETECTOR, TargetType.UNTYPED):
        return [c for c in detected_text if c in present][:1]
    return [c for c in DEFAULT_CONTENT_COLUMNS[target_type] if c in present]


def initial_roles(
    target_type: str,
    columns: Sequence[str],
    *,
    detected_text: Sequence[str] = (),
    parent_roles: Mapping[str, str] | None = None,
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Every user column's role, then the system columns (FR-002.20, FR-002.23).

    A version input's roles carry over; otherwise the target type's defaults apply. Overrides from
    the request win. Every other user column is metadata. No content column refuses the build.
    """
    ordered: list[str] = []
    for column in columns:
        if column.startswith(RESERVED_PREFIX):
            continue
        if column not in ordered:
            ordered.append(column)
    if parent_roles is not None:
        roles = {c: parent_roles.get(c, ColumnRole.METADATA.value) for c in ordered}
        roles = {
            c: (r if r != ColumnRole.SYSTEM else ColumnRole.METADATA.value)
            for c, r in roles.items()
        }
    else:
        content = set(default_content_columns(target_type, ordered, detected_text))
        roles = {
            c: (ColumnRole.CONTENT.value if c in content else ColumnRole.METADATA.value)
            for c in ordered
        }
    for column, role in (overrides or {}).items():
        if column not in roles:
            raise NotFoundError(
                f"column_roles names {column!r}, which no input has. Inputs have {ordered}.",
                code="column_not_found",
                details={"column": column},
            )
        roles[column] = role
    if not any(r == ColumnRole.CONTENT for r in roles.values()):
        raise AppError(
            f"No content column for a {target_type} version: the row key needs at least one. "
            f"Mark the text column as content in column_roles (inputs have {ordered}).",
            code="no_content_columns",
            status_code=422,
            details={"target_type": target_type, "columns": ordered},
        )
    for name in SYSTEM_COLUMNS:
        roles[name] = ColumnRole.SYSTEM.value
    return roles


def content_columns(roles: Mapping[str, str]) -> list[str]:
    return sorted(c for c, r in roles.items() if r == ColumnRole.CONTENT)


# --------------------------------------------------------------------------------------------
# Plans: what assembly reads, resolved from the database by the orchestrator
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class InputFile:
    """One file to read: a source file (by split) or a version's split file."""

    path: Path
    sha256: str
    split: str
    kind: str  # "source" | "version"
    source_id: str | None
    label: str  # for messages: the source or version and the split


@dataclass(frozen=True)
class AssemblyResult:
    rows: int
    logical_digest: str
    column_roles: dict[str, str]
    parts: list[Path]


def verify_files(
    files: Sequence[InputFile], progress: Callable[[int, int], None] | None = None
) -> None:
    """Re-hash every input file against its recorded SHA-256 before reading (FR-002.7)."""
    for done, item in enumerate(files, start=1):
        actual = file_sha256(item.path) if item.path.is_file() else "missing"
        if actual != item.sha256:
            raise BuildRefusal(
                f"{item.label} does not match the hash recorded when it was imported "
                f"({item.sha256[:12]}… recorded, {actual[:12]}… on disk). The file changed after "
                "import; import the source again.",
                code="source_hash_mismatch",
                details={"file": item.label, "expected": item.sha256, "actual": actual},
            )
        if progress is not None:
            progress(done, len(files))


def _user_schema(files: Sequence[InputFile]) -> pa.Schema:
    """The union of user columns across inputs, first-appearance order; types must agree."""
    fields: dict[str, pa.Field] = {}
    origin: dict[str, str] = {}
    for item in files:
        for f in pq.read_schema(item.path):
            if f.name.startswith(RESERVED_PREFIX):
                if item.kind == "source":
                    raise BuildRefusal(
                        f"{item.label} has a column {f.name!r}; the _dw_ prefix is reserved for "
                        "miDataworks. Rename the column in the source and import it again.",
                        code="reserved_column",
                        details={"file": item.label, "column": f.name},
                    )
                continue
            seen = fields.get(f.name)
            if seen is None:
                fields[f.name] = f.with_nullable(True)
                origin[f.name] = item.label
            elif not seen.type.equals(f.type):
                if pa.types.is_null(seen.type):
                    fields[f.name] = f.with_nullable(True)
                elif not pa.types.is_null(f.type):
                    raise BuildRefusal(
                        f"Column {f.name!r} is {seen.type} in {origin[f.name]} but {f.type} in "
                        f"{item.label}. Make the types agree with an operator before combining "
                        "these inputs.",
                        code="input_schema_conflict",
                        details={"column": f.name, "types": [str(seen.type), str(f.type)]},
                    )
    return pa.schema(list(fields.values()))


def _batches(item: InputFile, rows_per_batch: int) -> Iterator[pa.RecordBatch]:
    handle = pq.ParquetFile(item.path)
    yield from handle.iter_batches(batch_size=rows_per_batch)


def assemble(
    files: Sequence[InputFile],
    roles: Mapping[str, str],
    scheme: str,
    output_dir: Path,
    *,
    check_cancel: Callable[[], None] | None = None,
    progress: Callable[[int], None] | None = None,
) -> AssemblyResult:
    """Write the build's input set into ``output_dir`` (a staging directory the caller renames)."""
    rows_per_group = get_settings().parquet_row_group_rows
    user_schema = _user_schema(files)
    schema = user_schema
    for name, arrow_type in SYSTEM_COLUMNS.items():
        schema = schema.append(pa.field(name, arrow_type))
    keys_of = content_columns(roles)
    occurrences: dict[str, int] = {}
    output_dir.mkdir(parents=True, exist_ok=True)
    part = output_dir / "part-00000.parquet"
    total = 0
    index_in_split: dict[tuple[str, str], int] = {}
    with pq.ParquetWriter(part, schema, compression="zstd") as writer:
        for item in files:
            for batch in _batches(item, rows_per_group):
                if check_cancel is not None:
                    check_cancel()
                table = pa.Table.from_batches([batch])
                columns: dict[str, Any] = {}
                for f in user_schema:
                    if f.name in table.column_names:
                        columns[f.name] = table.column(f.name).cast(f.type)
                    else:
                        columns[f.name] = pa.nulls(table.num_rows, f.type)
                user_rows = pa.table(columns, schema=user_schema).select(keys_of).to_pylist()
                keys = [compute_row_key(r, keys_of, scheme) for r in user_rows]
                occ: list[int] = []
                for key in keys:
                    n = occurrences.get(key, 0)
                    occ.append(n)
                    occurrences[key] = n + 1
                n_rows = table.num_rows
                if item.kind == "source":
                    start = index_in_split.get((str(item.source_id), item.split), 0)
                    index_in_split[(str(item.source_id), item.split)] = start + n_rows
                    system = {
                        "_dw_split": pa.array([item.split] * n_rows, pa.string()),
                        "_dw_origin": pa.array([Origin.SOURCE.value] * n_rows, pa.string()),
                        "_dw_source_id": pa.array([item.source_id] * n_rows, pa.string()),
                        "_dw_source_locator": pa.array(
                            [f"{item.split}:{start + i}" for i in range(n_rows)], pa.string()
                        ),
                        "_dw_parent_keys": pa.nulls(n_rows, pa.list_(pa.string())),
                    }
                else:
                    system = {
                        name: table.column(name).cast(SYSTEM_COLUMNS[name])
                        for name in (
                            "_dw_split",
                            "_dw_origin",
                            "_dw_source_id",
                            "_dw_source_locator",
                            "_dw_parent_keys",
                        )
                    }
                columns["_dw_row_key"] = pa.array(keys, pa.string())
                columns["_dw_occurrence"] = pa.array(occ, pa.int32())
                columns.update(system)
                writer.write_table(pa.table(columns, schema=schema), row_group_size=rows_per_group)
                total += n_rows
                if progress is not None:
                    progress(total)
    logger.info("assemble wrote %d rows from %d files", total, len(files))
    return AssemblyResult(
        rows=total, logical_digest=logical_digest(part), column_roles=dict(roles), parts=[part]
    )
