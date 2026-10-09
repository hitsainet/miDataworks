"""Rows to integer codes for the statistics, and the one place a version's files are read for them
(FTID 004 §11).

``load_inputs`` reads the split files of one or more ``ReportInput``s through DuckDB with ONLY the
columns asked for, ordered by ``(_dw_row_key, _dw_occurrence)`` so folds and draws are the same
whatever order the Parquet files hold. A cross-role input adds a literal ``role`` column. Paths come
from the version row (built by feature 002 under ``DATA_DIR``); no request ever supplies a path.

``encode`` turns one column into ``(codes, names)``: numeric columns are cut into decile bands
(edges returned), everything else is dictionary-encoded as text, and null is its own value.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from ...core.storage import resolve_under_data_dir
from ..duck import connect, files_param
from .binning import NULL_LABEL, band_index, band_label, quantile_edges

ORDER_COLUMNS = ("_dw_row_key", "_dw_occurrence")
ROLE_COLUMN = "role"


@dataclass(frozen=True)
class ReportInput:
    """One input to a report: a version, optionally one split of it, optionally a role name."""

    version_id: str
    split: str | None = None
    role: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"version_id": str(self.version_id)}
        if self.split is not None:
            out["split"] = self.split
        if self.role is not None:
            out["role"] = self.role
        return out


def as_inputs(inputs: Sequence[Any]) -> list[ReportInput]:
    """Accept ``ReportInput``s, ``(version_id, split)`` tuples (feature 008's shape), dicts, or
    bare version ids. Order is kept; duplicates are removed."""
    out: list[ReportInput] = []
    for item in inputs:
        if isinstance(item, ReportInput):
            ri = item
        elif isinstance(item, dict):
            ri = ReportInput(str(item["version_id"]), item.get("split"), item.get("role"))
        elif isinstance(item, tuple | list):
            ri = ReportInput(
                str(item[0]),
                item[1] if len(item) > 1 else None,
                item[2] if len(item) > 2 else None,
            )
        else:
            ri = ReportInput(str(item))
        if ri not in out:
            out.append(ri)
    return out


@dataclass
class InputFiles:
    input: ReportInput
    #: ``[(split name, absolute path)]``.
    files: list[tuple[str, Any]] = field(default_factory=list)


def files_for(version: Any, ri: ReportInput) -> InputFiles:
    """The split files a report input reads, from the version row's ``splits``."""
    chosen = [s for s in version.splits if ri.split is None or s["name"] == ri.split]
    return InputFiles(ri, [(s["name"], resolve_under_data_dir(s["path"])) for s in chosen])


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def load_table(
    sources: Sequence[InputFiles], columns: Sequence[str], *, cross_role: bool = False
) -> pa.Table:
    """Project ``columns`` (those present) from every input, ordered by row key and occurrence."""
    selects: list[str] = []
    params: list[Any] = []
    con = connect()
    try:
        for source in sources:
            paths = [p for _, p in source.files]
            if not paths:
                continue
            present = con.execute(
                "SELECT name FROM parquet_schema(?) WHERE name IS NOT NULL", [files_param(paths)]
            ).fetchall()
            names = {str(r[0]) for r in present}
            parts = [quote(c) if c in names else f"NULL AS {quote(c)}" for c in columns]
            if cross_role:
                parts.append(f"? AS {quote(ROLE_COLUMN)}")
            sql = (
                f"SELECT {', '.join(parts)} FROM read_parquet(?, union_by_name=true)"  # noqa: S608
            )
            if cross_role:
                params.append(source.input.role or source.input.split or source.input.version_id)
            params.append(files_param(paths))
            selects.append(sql)
        if not selects:
            fields = [pa.field(c, pa.string()) for c in columns]
            return pa.schema(fields).empty_table()
        order = ", ".join(quote(c) for c in ORDER_COLUMNS if c in columns)
        query = " UNION ALL BY NAME ".join(f"({s})" for s in selects)
        if order:
            query = f"SELECT * FROM ({query}) ORDER BY {order}"  # noqa: S608 - quoted names
        return con.execute(query, params).to_arrow_table()
    finally:
        con.close()


def schema_names(sources: Sequence[InputFiles]) -> dict[str, pa.DataType]:
    """Column name -> Arrow type over the inputs' files (first file of each input)."""
    import pyarrow.parquet as pq

    out: dict[str, pa.DataType] = {}
    for source in sources:
        for _, path in source.files[:1]:
            for fld in pq.read_schema(path):
                out.setdefault(fld.name, fld.type)
    return out


def is_numeric(dtype: pa.DataType) -> bool:
    return (pa.types.is_integer(dtype) or pa.types.is_floating(dtype)) and not pa.types.is_boolean(
        dtype
    )


@dataclass
class Encoded:
    codes: np.ndarray
    names: list[str]
    #: Band edges for a binned column, else None.
    edges: list[float] | None = None


def encode(array: pa.ChunkedArray | pa.Array, *, binned: bool | None = None) -> Encoded:
    """Integer codes for one column; nulls are their own code named ``(empty)``."""
    if isinstance(array, pa.ChunkedArray):
        array = array.combine_chunks()
    numeric = is_numeric(array.type) if binned is None else binned
    if numeric:
        values = np.asarray(
            pc.fill_null(pc.cast(array, pa.float64()), float("nan")).to_numpy(zero_copy_only=False),
            dtype=np.float64,
        )
        edges = quantile_edges(values)
        bands = band_index(values, edges)
        present = sorted(set(bands.tolist()))
        remap = {b: i for i, b in enumerate(present)}
        codes = np.fromiter((remap[b] for b in bands), dtype=np.int64, count=len(bands))
        return Encoded(codes, [band_label(b, edges) for b in present], edges)
    if not pa.types.is_string(array.type) and not pa.types.is_large_string(array.type):
        array = _as_text(array)
    encoded = pc.dictionary_encode(array)
    dictionary = [str(v) for v in encoded.dictionary.to_pylist()]
    indices = encoded.indices
    null_code = len(dictionary)
    codes = np.asarray(
        pc.fill_null(indices, null_code).to_numpy(zero_copy_only=False), dtype=np.int64
    )
    names = dictionary + ([NULL_LABEL] if indices.null_count else [])
    return Encoded(codes, names)


def _as_text(array: pa.Array) -> pa.Array:
    try:
        return pc.cast(array, pa.string())
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
        return pa.array(
            [None if v is None else str(v) for v in array.to_pylist()], type=pa.string()
        )


def label_codes(array: pa.ChunkedArray | pa.Array) -> tuple[np.ndarray, list[str], np.ndarray]:
    """Label codes in SORTED class-name order (ties go to the lowest code, so the order must not
    depend on file order), plus the mask of rows whose label is present."""
    if isinstance(array, pa.ChunkedArray):
        array = array.combine_chunks()
    text = _as_text(array) if not pa.types.is_string(array.type) else array
    present = np.asarray(pc.is_valid(text).to_numpy(zero_copy_only=False), dtype=bool)
    values = text.to_pylist()
    names = sorted({v for v in values if v is not None})
    index = {name: i for i, name in enumerate(names)}
    codes = np.fromiter((index.get(v, -1) if v is not None else -1 for v in values), np.int64)
    return codes, names, present
