"""Helpers for feature 003's tests: rows with REAL row keys, parts on disk, specs, registries.

Rows are keyed with feature 002's ``compute_row_key`` over the content columns, so a fixture can
never agree with conservation's silent-change check by construction (a hand-written key would).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from src.operators.executor import GenerationStageSpec
from src.operators.manifest import manifest_hash
from src.operators.registry import OperatorRegistry
from src.services.row_keys import ROWKEY_V1, compute_row_key

ROLES = {"text": "content", "note": "metadata"}
SCHEMA = pa.schema(
    [
        ("text", pa.string()),
        ("note", pa.string()),
        ("_dw_row_key", pa.string()),
        ("_dw_occurrence", pa.int32()),
        ("_dw_split", pa.string()),
    ]
)

#: Lengths across the drop_short cut, an exact duplicate, a whitespace row and a non-English row.
TEXTS = [
    "short",
    "a considerably longer sentence that survives",
    "mid length row",
    "a considerably longer sentence that survives",
    "   ",
    "une phrase en français assez longue",
    "x",
    "another long enough row of plain text",
]


def rows(texts: list[str] | None = None, split: str | None = "train") -> list[dict[str, Any]]:
    out = []
    seen: dict[str, int] = {}
    for i, text in enumerate(texts if texts is not None else TEXTS):
        key = compute_row_key({"text": text}, ["text"], ROWKEY_V1)
        occurrence = seen.get(key, 0)
        seen[key] = occurrence + 1
        out.append(
            {
                "text": text,
                "note": f"n{i}",
                "_dw_row_key": key,
                "_dw_occurrence": occurrence,
                "_dw_split": split,
            }
        )
    return out


def table(texts: list[str] | None = None) -> pa.Table:
    return pa.Table.from_pylist(rows(texts), schema=SCHEMA)


def write_parts(directory: Path, data: pa.Table, parts: int = 1) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    size = max(1, -(-data.num_rows // parts))
    for index in range(parts):
        chunk = data.slice(index * size, size)
        pq.write_table(chunk, directory / f"part-{index:05d}.parquet")
    return directory


def registry() -> OperatorRegistry:
    """The fixture registry (no entry points, so no database read)."""
    from src.operators.native.fixtures import FIXTURE_OPERATORS

    return OperatorRegistry.build(native=FIXTURE_OPERATORS, catalogues=(), entry_points=())


def stage_spec(
    reg: OperatorRegistry,
    name: str,
    params: dict[str, Any] | None = None,
    *,
    data: pa.Table | None = None,
    input_dir: str | None = None,
    seed: int = 11,
    output_dir: str | None = None,
    body_overrides: dict[str, Any] | None = None,
) -> GenerationStageSpec:
    entry = reg.entry(name, "1")
    assert entry.manifest is not None
    return GenerationStageSpec(
        job_id="job-test",
        operator=name,
        version="1",
        expected_manifest_hash=manifest_hash(entry.manifest),
        params=params or {},
        output_dir=output_dir or f"staging/stage-{uuid.uuid4().hex}",
        step_seed=seed,
        column_roles=dict(ROLES),
        rowkey_scheme=ROWKEY_V1,
        input_dir=input_dir,
        input_table=data if input_dir is None else None,
        body_overrides=body_overrides,
    )
