"""Benchmark the finalize work 021 adds (FTASKS 18.1; 021-D24; FTID §13). No CI threshold.

Writes a version's split files (row keys, origin, a text column) for N rows under a temporary data
directory and times ``wsp_partitions.check_finalize`` on them: a version with WSP marks (a practice
split, a held-out split and a long held-out split with a floor), and the same files with no marks
(the path a non-WSP build takes, which must add nothing measurable). Prints seconds and peak memory
(Python allocations through ``tracemalloc``, and the process's maximum resident set).

The lineage step (5) needs version inputs in the database; it runs the same key join as step (4),
once per input held-out split, so step (4)'s figure bounds it per split.

Usage: ``cd backend && python -m scripts.bench_wsp_finalize [rows ...]`` (default 33550 1000000).
"""

from __future__ import annotations

import hashlib
import os
import resource
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


def _write(path: Path, start: int, n: int, *, long_text: bool) -> None:
    keys = [hashlib.sha256(f"row-{i}".encode()).hexdigest() for i in range(start, start + n)]
    texts = [
        ("long " * 40 if long_text else "") + f"row {i} of the benchmark, text body"
        for i in range(start, start + n)
    ]
    pq.write_table(
        pa.table(
            {
                "_dw_row_key": pa.array(keys, pa.string()),
                "_dw_origin": pa.array(["source"] * n, pa.string()),
                "text": pa.array(texts, pa.string()),
            }
        ),
        path,
    )


def run(rows: int) -> dict[str, float]:
    from src.core.config import get_settings
    from src.services import wsp_partitions

    root = Path(tempfile.mkdtemp(prefix="bench-wsp-"))
    get_settings().data_dir = root  # type: ignore[misc]
    staged = root / "staging"
    staged.mkdir()
    sizes = {"fit": rows * 8 // 10, "hold": rows // 10, "long": rows - rows * 9 // 10}
    written: list[tuple[str, Path]] = []
    start = 0
    for name, n in sizes.items():
        path = staged / f"{name}.parquet"
        _write(path, start, n, long_text=name == "long")
        written.append((name, path))
        start += n
    mark = {"text_column": "text", "min_chars": 0}
    marked = {
        "fit": {
            "held_out": False,
            "wsp": {**mark, "role": "wsp_practice", "set_kind": "in_distribution"},
        },
        "hold": {
            "held_out": True,
            "wsp": {**mark, "role": "wsp_heldout", "set_kind": "everyday_text"},
        },
        "long": {
            "held_out": True,
            "wsp": {
                "role": "wsp_heldout",
                "set_kind": "long_untruncated",
                "text_column": "text",
                "min_chars": 200,
            },
        },
    }
    plain = {name: {"held_out": role["held_out"]} for name, role in marked.items()}
    out: dict[str, float] = {}
    # warm the lazy imports (DuckDB, pyarrow.compute) outside the timed calls
    wsp_partitions.check_finalize(
        None, written[:1], {"fit": marked["fit"]}, [], roles_declared_by_step=True
    )
    for label, roles in (("no_marks", plain), ("wsp_marks", marked)):
        tracemalloc.start()
        t0 = time.perf_counter()
        wsp_partitions.check_finalize(None, written, roles, [], roles_declared_by_step=True)
        out[f"{label}_seconds"] = time.perf_counter() - t0
        out[f"{label}_peak_mib"] = tracemalloc.get_traced_memory()[1] / 2**20
        tracemalloc.stop()
    out["max_rss_mib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    return out


def main(argv: list[str]) -> None:
    os.environ.setdefault("OPERATOR_TEST_FIXTURES", "false")
    for rows in [int(a) for a in argv] or [33_550, 1_000_000]:
        result = run(rows)
        print(
            f"rows={rows:>9,}  no marks {result['no_marks_seconds']:.4f} s "
            f"(peak {result['no_marks_peak_mib']:.1f} MiB)  WSP marks {result['wsp_marks_seconds']:.3f} s "
            f"(peak {result['wsp_marks_peak_mib']:.1f} MiB)  max RSS {result['max_rss_mib']:.0f} MiB",
            flush=True,
        )


if __name__ == "__main__":
    main(sys.argv[1:])
