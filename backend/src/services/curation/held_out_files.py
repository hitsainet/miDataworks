"""The disjointness proof's facts read from WRITTEN split files (feature 017 FR-017.2, FR-017.8).

Finalize's authoritative proof, the re-check script (``backend/scripts/check_held_out_proof.py``)
and D-11's cross-version proof all read Parquet split files here, then decide with the one pure
function ``held_out_proof.prove``. Values are read with PyArrow and turned to text with ``str``,
exactly as ``split@1.1.0`` reads its input (``curation.common.text_column``), so the step and the
files can never disagree on how a value is spelled (a DuckDB ``CAST`` writes ``true``, ``str``
writes ``True``). A directing column a file lacks reads as null in every row of it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .held_out_proof import Combo, DirectedBy, Facts, Proof, prove

ORIGIN = "_dw_origin"


def _columns(path: Path) -> set[str]:
    return set(pq.read_schema(path).names)


def combos(paths: Sequence[Path], columns: Sequence[str]) -> dict[Combo, int]:
    """Row count per combination of ``columns`` values over the files (None = null or absent)."""
    out: dict[Combo, int] = {}
    for path in paths:
        present = [c for c in columns if c in _columns(path)]
        if not present:
            rows = pq.ParquetFile(path).metadata.num_rows
            if rows:
                key: Combo = tuple(None for _ in columns)
                out[key] = out.get(key, 0) + rows
            continue
        table = pq.read_table(path, columns=present)
        if table.num_rows == 0:
            continue
        counted = table.group_by(present).aggregate([([], "count_all")]).to_pylist()
        for row in counted:
            key = tuple(
                (None if row[c] is None else str(row[c])) if c in present else None for c in columns
            )
            out[key] = out.get(key, 0) + int(row["count_all"])
    return out


def groups(paths: Sequence[Path], column: str) -> set[str]:
    """The distinct non-null values of a group column over the files."""
    found: set[str] = set()
    for path in paths:
        if column not in _columns(path):
            continue
        values = pc.unique(pq.read_table(path, columns=[column]).column(0))
        found |= {str(v) for v in values.to_pylist() if v is not None}
    return found


def generated(paths: Sequence[Path]) -> int:
    total = 0
    for path in paths:
        if ORIGIN not in _columns(path):
            continue
        column = pq.read_table(path, columns=[ORIGIN]).column(0)
        total += int(pc.sum(pc.equal(column, pa.scalar("generated"))).as_py() or 0)
    return total


def facts(
    directed: DirectedBy,
    held_out: Sequence[Path],
    training: Mapping[str, Path | Sequence[Path]],
) -> Facts:
    """The proof's facts for one directed split's files against the training splits' files."""
    train_paths: list[Path] = []
    for value in training.values():
        train_paths.extend([value] if isinstance(value, Path) else list(value))
    return Facts(
        held_out_rows=combos(held_out, directed.all_columns),
        training_rows=combos(train_paths, directed.all_columns),
        shared_groups={
            g: len(groups(held_out, g) & groups(train_paths, g)) for g in directed.group_columns
        },
        generated_rows=generated(held_out),
        training_splits=tuple(training),
    )


def prove_files(
    split: str,
    directed: DirectedBy,
    held_out: Sequence[Path],
    training: Mapping[str, Path | Sequence[Path]],
) -> Proof:
    """The proof of a directed split, recomputed over written (or published) files."""
    return prove(split, directed, facts(directed, held_out, training))
