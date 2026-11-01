"""Train/evaluation leakage across splits or roles (FR-004.20, 004.21, 004.50; FTID 004 §7.6).

The "sides" of a check are the inputs: ``(version, split)`` pairs (feature 008's C-3 passes one per
split) or ``(version, split, role)`` triples (feature 009's D-4). A single bare version is checked
across its own splits. Three kinds of crossing are reported, each as counts per side pair keyed
``"<a>|<b>"`` (names sorted) and as listed pairs:

- **exact**: rows with the same comparison key (feature 002's row key) on two sides;
- **near**: MinHash candidates verified at or above the threshold, across sides (``basis: lexical``;
  embeddings after M1, P-18);
- **group**: rows sharing a value of a named group column on two sides (two edits of one
  Humicroedit headline, ``scripts/paired_probe.py``) — texts may differ, the topic still leaks.

The pairs are written to ``runs/<report id>/reports/leakage_pairs.parquet`` with excerpts.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy.orm import Session

from ...core.config import get_settings
from ...core.storage import run_dir, staged_path
from ...models.enums import ColumnRole
from ..identity import file_sha256
from . import minhash
from .audit_service import excerpt
from .codes import ReportInput, files_for, load_table, schema_names
from .errors import CurationError
from .text_stats import as_text

DEFAULT_THRESHOLD = 0.8
DEFAULT_SHINGLE = "word"
DEFAULT_SIZE = 5
PAIRS_FILE = "leakage_pairs.parquet"
SIDE = "_side"
#: Per-input grouping (009 detector sets): one string column ``"<column>\x1f<value>"`` per row, so
#: each input is grouped by ITS OWN column and an input that declared none contributes no group.
GROUP = "_dw_group"
GROUP_SEP = "\x1f"


def leakage_params(
    group_column: str | None,
    threshold: float | None,
    group_columns: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """``group_columns`` (role -> that role's group column, or ``None`` for no grouping) groups
    each input by its OWN column; it is added only when given, so a report keyed by the ordinary
    params keeps its identity."""
    params: dict[str, Any] = {
        "group_column": group_column,
        "threshold": float(threshold if threshold is not None else DEFAULT_THRESHOLD),
        "permutations": get_settings().curation_minhash_permutations,
        "shingle": DEFAULT_SHINGLE,
        "size": DEFAULT_SIZE,
    }
    if group_columns is not None:
        if group_column is not None:
            raise ValueError("give group_column or group_columns, not both")
        params["group_columns"] = dict(sorted(group_columns.items()))
    return params


@dataclass
class LeakageResult:
    """FTDD §4.2; feature 008's seam reads ``exact_pairs``, ``near_pairs`` and ``group_pairs``."""

    inputs: list[dict[str, Any]]
    exact_pairs: dict[str, int] = field(default_factory=dict)
    near_pairs: dict[str, int] = field(default_factory=dict)
    group_pairs: dict[str, int] = field(default_factory=dict)
    pairs_artefact: str | None = None
    basis: str = "lexical"
    threshold: float = DEFAULT_THRESHOLD
    group_column: str | None = None
    report_id: str | None = None
    n_rows: int = 0

    @classmethod
    def from_report(cls, report: Any) -> LeakageResult:
        r = report.result
        return cls(
            inputs=list(report.inputs),
            exact_pairs=dict(r["exact_pairs"]),
            near_pairs=dict(r["near_pairs"]),
            group_pairs=dict(r["group_pairs"]),
            pairs_artefact=r.get("pairs_artefact"),
            basis=r["basis"],
            threshold=float(r["threshold"]),
            group_column=r.get("group_column"),
            report_id=report.id,
            n_rows=int(r["n_rows"]),
        )

    @property
    def total(self) -> int:
        return (
            sum(self.exact_pairs.values())
            + sum(self.near_pairs.values())
            + sum(self.group_pairs.values())
        )


def _key(a: str, b: str) -> str:
    x, y = sorted((a, b))
    return f"{x}|{y}"


def load_sides(
    session: Session,
    inputs: Sequence[ReportInput],
    group_column: str | None,
    group_columns: dict[str, str | None] | None = None,
) -> tuple[pa.Table, list[str]]:
    """Every input's rows with a ``_side`` column; a bare version contributes one side per split.

    With ``group_columns`` each input (by its role) is grouped by its own column into ``GROUP``;
    an input whose role declared no column has no group, while one that DECLARED a column its
    version lacks is refused, naming the role."""
    from .api import require_version

    tables = []
    content: set[str] = set()
    for ri in inputs:
        version = require_version(session, ri.version_id)
        content |= {c for c, r in version.column_roles.items() if r == ColumnRole.CONTENT}
        source = files_for(version, ri)
        own = (group_columns or {}).get(str(ri.role)) if group_columns is not None else None
        for split, path in source.files:
            one = type(source)(ri, [(split, path)])
            names = schema_names([one])
            if group_column is not None and group_column not in names:
                raise CurationError(
                    "group_column_missing",
                    f"The group column {group_column!r} is not in {ri.version_id}; choose a "
                    "column the version has.",
                    {"group_column": group_column},
                )
            if own is not None and own not in names:
                raise CurationError(
                    "group_column_missing",
                    f"Role {ri.role} declares the group column {own!r}, which is not in "
                    f"{ri.version_id}; choose a column that version has.",
                    {"group_column": own, "role": ri.role, "version_id": str(ri.version_id)},
                )
            wanted = ["_dw_row_key", "_dw_occurrence", *sorted(c for c in content if c in names)]
            if group_column:
                wanted.append(group_column)
            if own is not None and own not in wanted:
                wanted.append(own)
            table = load_table([one], wanted)
            if group_columns is not None:
                values = table.column(own).to_pylist() if own is not None else []
                groups = (
                    [None if v is None else f"{own}{GROUP_SEP}{v}" for v in values]
                    if own is not None
                    else [None] * table.num_rows
                )
                if own is not None and own not in content:
                    table = table.drop_columns([own])
                table = table.append_column(GROUP, pa.array(groups, pa.string()))
            name = ri.role or split
            tables.append(table.append_column(SIDE, pa.array([name] * table.num_rows, pa.string())))
    sides = sorted({str(t.column(SIDE)[0]) for t in tables if t.num_rows})
    if len(sides) < 2:
        raise CurationError(
            "no_splits",
            "Leakage is measured between splits or roles, and these inputs have only "
            f"{len(sides)}. Add a split step, or check several roles together.",
            {"sides": sides},
        )
    merged = pa.concat_tables(tables, promote_options="default")
    return merged, sorted(content)


def check_table(
    table: pa.Table, content: Sequence[str], params: dict[str, Any], seed: int
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Exact, near and group crossings between the ``_side`` values of ``table``."""
    group_column = params.get("group_column")
    per_input = params.get("group_columns") is not None
    group_source = GROUP if per_input else group_column
    n = table.num_rows
    sides = [str(s) for s in table.column(SIDE).to_pylist()]
    keys = [str(k) for k in table.column("_dw_row_key").to_pylist()]
    columns = [table.column(c).to_pylist() for c in content if c in table.schema.names]
    texts = ["\n".join(as_text(v) for v in vals) for vals in zip(*columns, strict=True)]
    if not columns:
        texts = [""] * n
    pairs: list[dict[str, Any]] = []
    exact: dict[str, int] = {}
    near: dict[str, int] = {}
    group: dict[str, int] = {}

    def add(
        kind: str, i: int, j: int, statistic: float, bucket: dict[str, int], shared: str
    ) -> None:
        if sides[i] == sides[j]:
            return
        name = _key(sides[i], sides[j])
        bucket[name] = bucket.get(name, 0) + 1
        pairs.append(
            {
                "kind": kind,
                "side_a": sides[i],
                "side_b": sides[j],
                "row_key_a": keys[i],
                "row_key_b": keys[j],
                "statistic": statistic,
                "shared": shared,
                "excerpt_a": excerpt(texts[i]),
                "excerpt_b": excerpt(texts[j]),
            }
        )

    by_key: dict[str, list[int]] = {}
    for i, k in enumerate(keys):
        by_key.setdefault(k, []).append(i)
    for k, rows in by_key.items():
        for i, j in combinations(rows, 2):
            add("exact", i, j, 1.0, exact, k)
    threshold = float(params["threshold"])
    sigs = minhash.signatures_for(
        texts,
        kind=params["shingle"],
        size=int(params["size"]),
        count=int(params["permutations"]),
        seed=seed,
    )
    layout = minhash.band_layout(int(params["permutations"]), threshold)
    _, verified = minhash.near_duplicate_groups(sigs, layout, threshold)
    for (i, j), estimate in verified.items():
        if keys[i] != keys[j]:  # identical keys are already exact pairs
            add("near", i, j, estimate, near, "")
    if group_source:
        values = table.column(group_source).to_pylist()
        by_group: dict[str, list[int]] = {}
        for i, v in enumerate(values):
            if v is not None:
                by_group.setdefault(str(v), []).append(i)
        for value, rows in by_group.items():
            if len({sides[i] for i in rows}) < 2:
                continue
            shared = value.split(GROUP_SEP, 1)[-1] if per_input else value
            for i, j in combinations(rows, 2):
                add("group", i, j, 1.0, group, shared)
    result = {
        "sides": sorted(set(sides)),
        "exact_pairs": exact,
        "near_pairs": near,
        "group_pairs": group,
        "pairs_artefact": None,
        "basis": "lexical",
        "threshold": threshold,
        "band_layout": {"bands": layout.bands, "rows": layout.rows},
        "group_column": group_column,
        **({"group_columns": params["group_columns"]} if per_input else {}),
        "n_rows": n,
        "pairs_total": len(pairs),
    }
    return result, pairs


def check(
    session: Session,
    inputs: Sequence[ReportInput],
    params: dict[str, Any],
    seed: int,
    *,
    report_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    table, content = load_sides(
        session, inputs, params.get("group_column"), params.get("group_columns")
    )
    result, pairs = check_table(table, content, params, seed)
    artefacts: list[dict[str, Any]] = []
    if pairs:
        destination = run_dir(report_id) / "reports" / PAIRS_FILE
        destination.parent.mkdir(parents=True, exist_ok=True)
        with staged_path(destination) as staged:
            pq.write_table(pa.Table.from_pylist(pairs), staged)
        path_text = f"runs/{report_id}/reports/{PAIRS_FILE}"
        result["pairs_artefact"] = path_text
        artefacts.append(
            {
                "name": PAIRS_FILE,
                "path": path_text,
                "sha256": file_sha256(destination),
                "rows": len(pairs),
            }
        )
    return result, artefacts, table.num_rows


def compute_leakage(
    session: Session, inputs: list[ReportInput], params: dict[str, Any], seed: int
) -> Any:
    from .report_service import Computed

    result, artefacts, rows = check(session, inputs, params, seed, report_id=str(uuid.uuid4()))
    return Computed(result=result, artefacts=artefacts, rows=rows)


def read_pairs(report: Any, *, page: int, limit: int) -> dict[str, Any]:
    """One page of a leakage report's pairs (the route never returns more than one page)."""
    from ...core.storage import resolve_under_data_dir

    path = (report.result or {}).get("pairs_artefact")
    if not path:
        return {"pairs": [], "total": 0}
    table = pq.read_table(resolve_under_data_dir(path))
    rows = table.slice(page * limit, limit).to_pylist()
    return {"pairs": rows, "total": table.num_rows}
