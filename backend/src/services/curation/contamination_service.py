"""Benchmark contamination: the catalogue, the overlap report, and pinned benchmark items
(FR-004.17–004.19; T-14; FTID 004 §7.8).

A benchmark is a feature 001 source at a pinned revision. It is never read at any other revision: a
source that is not ``ready`` or whose resolved commit differs from the pin refuses with
``benchmark_revision_unavailable``, naming both. Items are the source's text columns (001's
detection), one item per row.

Statistic: the share of a row's word n-grams (default n = 13) that occur in the benchmark, hashed
into a sorted ``uint64`` array and probed with ``np.isin``. For rows with any overlap the
best-matching item is recovered by a second, small lookup over the items sharing an n-gram.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pyarrow.parquet as pq
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.storage import resolve_under_data_dir
from ...models.enums import ColumnRole
from ...models.source import Source, SourceFile
from .audit_service import excerpt
from .codes import ReportInput, files_for, load_table, schema_names
from .errors import CurationError
from .text_stats import as_text, word_ngram_hashes

DEFAULT_N = 13
ROW_SAMPLE = 100

#: T-14: the C1 detector workloads' evaluation sets. The high-stakes sets are pinned when feature
#: 001 imports them (their revision is then the imported source's resolved commit).
CATALOGUE: tuple[dict[str, Any], ...] = (
    {
        "repo_id": "tasksource/humicroedit",
        "config": "subtask-1",
        "revision": "f5a16e65b0854032ab9b4c82ca182a6381b83bd5",
        "workload": "humor (C1)",
        "source": "records/datasets.md",
    },
    {
        "repo_id": None,
        "config": None,
        "revision": None,
        "workload": "high stakes (C1)",
        "source": "pinned when feature 001 imports the evaluation sets (T-14)",
    },
)


def catalogue(session: Session) -> list[dict[str, Any]]:
    out = []
    for entry in CATALOGUE:
        imported = None
        if entry["repo_id"] is not None:
            imported = session.execute(
                select(Source.id).where(
                    Source.repo_id == entry["repo_id"],
                    Source.resolved_commit == entry["revision"],
                    Source.state == "ready",
                )
            ).scalar_one_or_none()
        out.append(
            {
                **entry,
                "imported_source_id": str(imported) if imported else None,
                "imported": imported is not None,
            }
        )
    return out


@dataclass
class Benchmark:
    source_id: str
    name: str
    revision: str
    items: list[str]
    #: Sorted unique n-gram hashes, and for each item its hash set.
    index: np.ndarray
    item_hashes: list[set[int]]

    def label(self, item: int) -> str:
        return f"{self.name}@{self.revision}:{item}"


def load_benchmark(
    session: Session, source_id: str, n: int, revision: str | None = None
) -> Benchmark:
    source = session.get(Source, source_id)
    if source is None:
        raise CurationError("benchmark_not_found", f"No benchmark source {source_id}.")
    pinned = source.resolved_commit or source.content_hash
    if source.state != "ready" or pinned is None or (revision is not None and pinned != revision):
        raise CurationError(
            "benchmark_revision_unavailable",
            f"Benchmark {source.display_name} is not available at "
            f"{revision or 'its pinned revision'} (state {source.state}, "
            f"revision {pinned or 'none'}). Import it at the pinned revision; contamination is "
            "never checked against another revision.",
            {"source_id": source_id, "wanted": revision, "found": pinned},
        )
    text_columns = list((source.detection or {}).get("text_columns") or ["text"])
    files = session.execute(select(SourceFile).where(SourceFile.source_id == source_id)).scalars()
    items: list[str] = []
    for f in files:
        table = pq.read_table(resolve_under_data_dir(f.path))
        columns = [table.column(c).to_pylist() for c in text_columns if c in table.schema.names]
        items.extend("\n".join(as_text(v) for v in vals) for vals in zip(*columns, strict=True))
    item_hashes = [set(word_ngram_hashes(text, n)) for text in items]
    index = np.unique(np.fromiter((h for s in item_hashes for h in s), dtype=np.int64))
    return Benchmark(
        source_id, source.repo_id or source.display_name, pinned, items, index, item_hashes
    )


def overlaps(texts: list[str], bench: Benchmark, n: int) -> list[tuple[float, int | None]]:
    """Per row: share of its n-grams found in the benchmark, and the best item (or None)."""
    out: list[tuple[float, int | None]] = []
    for text in texts:
        hashes = np.array(word_ngram_hashes(text, n), dtype=np.int64)
        if hashes.size == 0:
            out.append((0.0, None))
            continue
        found = np.isin(hashes, bench.index)
        share = float(found.mean())
        best = None
        if share > 0:
            mine = set(hashes[found].tolist())
            scores = [
                (len(mine & item), i) for i, item in enumerate(bench.item_hashes) if mine & item
            ]
            best = max(scores)[1] if scores else None
        out.append((share, best))
    return out


def compute_contamination(
    session: Session, inputs: list[ReportInput], params: dict[str, Any], seed: int
) -> Any:
    from .api import require_version
    from .report_service import Computed

    n = int(params.get("n") or DEFAULT_N)
    version = require_version(session, inputs[0].version_id)
    content = sorted(c for c, r in version.column_roles.items() if r == ColumnRole.CONTENT)
    source = files_for(version, inputs[0])
    names = schema_names([source])
    table = load_table([source], ["_dw_row_key", *[c for c in content if c in names]])
    columns = [table.column(c).to_pylist() for c in content if c in names]
    texts = (
        ["\n".join(as_text(v) for v in vals) for vals in zip(*columns, strict=True)]
        if columns
        else []
    )
    keys = table.column("_dw_row_key").to_pylist()
    benchmarks = []
    for source_id in params["benchmark_source_ids"]:
        bench = load_benchmark(session, source_id, n)
        rows = []
        hit = 0
        best_share = 0.0
        for key, text, (share, item) in zip(keys, texts, overlaps(texts, bench, n), strict=True):
            if share > 0:
                hit += 1
                best_share = max(best_share, share)
                rows.append(
                    {
                        "row_key": key,
                        "overlap": share,
                        "item": bench.label(item) if item is not None else None,
                        "excerpt": excerpt(text),
                    }
                )
        rows.sort(key=lambda r: -r["overlap"])
        benchmarks.append(
            {
                "source_id": bench.source_id,
                "benchmark": bench.name,
                "revision": bench.revision,
                "items": len(bench.items),
                "rows_overlapping": hit,
                "max_overlap": best_share,
                "rows": rows[:ROW_SAMPLE],
            }
        )
    result = {
        "n": n,
        "n_rows": table.num_rows,
        "benchmarks": benchmarks,
        "statistic": f"share of word {n}-grams found in the benchmark",
    }
    return Computed(result=result, artefacts=[], rows=table.num_rows)
