"""Preview an operator against a SOURCE, not only a version (PII run L1, 2026-10-20).

The run built a no-op "base" version from every source before it could preview anything, because
an operator reads the build's system columns (``_dw_row_key``, ``_dw_occurrence``, ``_dw_split``
...) and a source's own files have none. Here a seeded sample of the source is given those columns
exactly as a build's assembly gives them (``services.assembly.assemble``): roles from the detected
text column or the caller's ``column_roles``, row keys with ``compute_row_key`` over the content
columns, the source locator from the row's position in its split's file. The sample is written once
per preview id under ``tmp/previews/`` and read by the preview task like a version's split.

Differences from a real build, stated in the answer (``input_note``): occurrences count duplicates
WITHIN THE SAMPLE only, and no recipe step has run.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from ..core.storage import resolve_under_data_dir, staged_path, tmp_dir
from ..models.enums import Origin
from ..services.assembly import initial_roles
from ..services.duck import arrow_table
from ..services.row_keys import ROWKEY_V1, compute_row_key
from ..services.step_contract import SYSTEM_COLUMNS
from .errors import OperatorError

PREVIEWS = "previews"
#: A materialised sample older than this is swept when another is written (results live 1 h).
KEEP_S = 2 * 3600
NOTE = (
    "Previewed on a seeded sample of the source with build system columns added; occurrences "
    "count duplicates within the sample only, and no recipe step has run."
)


def _sweep(root: Path) -> None:
    cutoff = time.time() - KEEP_S
    for child in root.iterdir() if root.is_dir() else ():
        if child.is_dir() and child.stat().st_mtime < cutoff:
            shutil.rmtree(child, ignore_errors=True)


def materialise(
    *,
    preview_id: str,
    source_id: str,
    files: Sequence[Any],
    split: str | None,
    detected_text: Sequence[str],
    overrides: Mapping[str, str],
    size: int,
    seed: int,
) -> dict[str, Any]:
    """Write the sample; return the preview's resolved input ``{files, column_roles, ...}``."""
    from .preview import _single_thread_connection

    chosen = [f for f in files if split is None or f.split == split]
    if not chosen:
        raise OperatorError(
            "input_not_found",
            f"The source has no split {split!r}." if split else "The source has no files.",
            {"source_id": source_id, "split": split},
        )
    columns: list[str] = []
    for f in chosen:
        for column in f.columns:
            if column["name"] not in columns:
                columns.append(column["name"])
    try:
        roles = initial_roles(
            "untyped", columns, detected_text=list(detected_text), overrides=dict(overrides)
        )
    except Exception as exc:  # no_content_columns, column_not_found: say what to do
        message = getattr(exc, "message", str(exc))
        raise OperatorError(
            "params_invalid",
            f'{message} Pass input.column_roles, for example {{"text": "content"}}.',
            {"columns": columns},
        ) from None
    root = tmp_dir() / PREVIEWS
    out = root / preview_id
    part = out / "part-00000.parquet"
    if not part.is_file():
        root.mkdir(parents=True, exist_ok=True)
        _sweep(root)
        by_path = {str(resolve_under_data_dir(f.path)): f.split for f in chosen}
        con = _single_thread_connection()
        try:
            rows, repeat = int(size), int(seed)
            table = arrow_table(
                con,
                "SELECT * FROM (SELECT * FROM read_parquet(?, union_by_name=true, "  # noqa: S608
                "filename=true, file_row_number=true) "
                f"USING SAMPLE reservoir({rows} ROWS) REPEATABLE ({repeat})) "
                "ORDER BY filename, file_row_number",
                [sorted(by_path)],
            )
        finally:
            con.close()
        out.mkdir(parents=True, exist_ok=True)
        with staged_path(part) as staged:
            pq.write_table(_with_system_columns(table, roles, source_id, by_path), staged)
    return {
        "files": [str(part.relative_to(resolve_under_data_dir()))],
        "column_roles": roles,
        "rowkey_scheme": ROWKEY_V1,
        "note": NOTE,
    }


def _with_system_columns(
    table: pa.Table, roles: Mapping[str, str], source_id: str, splits: Mapping[str, str]
) -> pa.Table:
    """The user columns plus ``SYSTEM_COLUMNS``, as ``assembly.assemble`` writes them."""
    content = sorted(c for c, r in roles.items() if r == "content")
    user = [c for c in table.column_names if c not in ("filename", "file_row_number")]
    rows = table.select(content).to_pylist()
    keys = [compute_row_key(r, content, ROWKEY_V1) for r in rows]
    seen: dict[str, int] = {}
    occurrence = []
    for key in keys:
        occurrence.append(seen.get(key, 0))
        seen[key] = seen.get(key, 0) + 1
    split_of = [splits[str(p)] for p in table.column("filename").to_pylist()]
    index = table.column("file_row_number").to_pylist()
    n = table.num_rows
    system = {
        "_dw_row_key": pa.array(keys, pa.string()),
        "_dw_occurrence": pa.array(occurrence, pa.int32()),
        "_dw_split": pa.array(split_of, pa.string()),
        "_dw_origin": pa.array([Origin.SOURCE.value] * n, pa.string()),
        "_dw_source_id": pa.array([source_id] * n, pa.string()),
        "_dw_source_locator": pa.array(
            [f"{s}:{i}" for s, i in zip(split_of, index, strict=True)], pa.string()
        ),
        "_dw_parent_keys": pa.nulls(n, pa.list_(pa.string())),
    }
    assert set(system) == set(SYSTEM_COLUMNS)
    arrays = {c: table.column(c) for c in user}
    arrays.update(system)
    return pa.table(arrays)
