"""Feature 017 slice 1 benchmarks on the changed paths (FTASKS 2.13; FTDD 017 section 9.2). Run by
hand, not collected (no database needed):

    cd backend && python -m tests.performance.bench_017 [rows]

On a seeded fixture (default 30,000 rows) for two behaviour-shaped configurations - ``alpha``
(directed by genre AND generator, carrier groups, identity comparison) and ``beta`` (directed by
genre only, carrier AND pair groups) - it times, through the code that ships:

- the split step through 003's executor: ``split@1.0.0`` and ``split@1.1.0`` with no new parameter
  (the unchanged path) and ``split@1.1.0`` directed (components, direction, the step's proof);
- finalize's proof over written Parquet files (``held_out_files.prove_files``), every directed split.

The census is slice 2 (not built yet): "not measured". Peak RSS is reported per path.
"""

from __future__ import annotations

import os
import resource
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://midataworks:midataworks-dev@127.0.0.1:55433/midataworks_test_017",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:56380/7")
os.environ.setdefault("SETTINGS_ENCRYPTION_KEY", "bench-only-encryption-key-0123456789abcdef")
os.environ.setdefault("INTERNAL_API_SECRET", "bench-only-internal-secret-0123456789")
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="bench017-"))

import numpy as np  # noqa: E402
import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

from src.core.storage import ensure_layout  # noqa: E402
from src.services.curation.held_out_files import prove_files  # noqa: E402
from src.services.curation.held_out_proof import DirectedBy  # noqa: E402

GENRES = [f"genre{i:02d}" for i in range(12)]
MODELS = ["gen-a@r1", "gen-b@r1", "gen-c@r1"]
ROLES = {
    "text": "content",
    "label": "metadata",
    "genre": "metadata",
    "generator": "metadata",
    "carrier_id": "metadata",
    "pair_id": "metadata",
}


def table(n: int) -> pa.Table:
    from tests.fixtures.humor_pool import with_system_columns

    rng = np.random.default_rng(17)
    rows = []
    for i in range(n):
        carrier = i // 2
        rows.append(
            {
                "text": f"carrier {carrier} variant {i % 2} " + " ".join(rng.choice(GENRES, 6)),
                "label": "pos" if rng.random() < 0.5 else "neg",
                "genre": GENRES[carrier % len(GENRES)],
                "generator": MODELS[(carrier // len(GENRES)) % len(MODELS)],
                "carrier_id": f"c{carrier}",
                # a pair links two carriers of one genre and generator (12 genres apart)
                "pair_id": f"p{carrier % len(GENRES)}-{carrier // (2 * len(GENRES))}",
            }
        )
    t = with_system_columns(rows)
    origin = ["generated" if i % 3 else "source" for i in range(n)]
    return t.set_column(t.schema.get_field_index("_dw_origin"), "_dw_origin", pa.array(origin))


BASE = {
    "split_names": ["train", "test", "ood_genre", "ood_generator", "ood_both"],
    "split_fractions": [0.8, 0.2, 0.0, 0.0, 0.0],
    "held_out": ["ood_genre", "ood_generator", "ood_both"],
    "stratify_by": ["label"],
}
CONFIGS: dict[str, dict[str, Any]] = {
    "alpha": {
        **BASE,
        "group_columns": ["carrier_id"],
        "directing_columns": ["genre", "generator"],
        "held_out_values": ["genre10", "genre11", "gen-c"],
        "held_out_value_columns": ["genre", "genre", "generator"],
        "identity_columns": ["generator"],
        "held_out_split_names": ["ood_genre", "ood_generator", "ood_both"],
    },
    "beta": {
        **BASE,
        "split_names": ["train", "test", "ood_genre"],
        "split_fractions": [0.8, 0.2, 0.0],
        "held_out": ["ood_genre"],
        "group_columns": ["carrier_id", "pair_id"],
        "directing_columns": ["genre"],
        "held_out_values": ["genre07"],
        "held_out_value_columns": ["genre"],
        "held_out_split_names": ["ood_genre"],
    },
}


def timed(label: str, fn: Any) -> Any:
    start = time.perf_counter()
    out = fn()
    seconds = time.perf_counter() - start
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(f"{label:<58} {seconds:8.3f} s   peak RSS {rss:7.0f} MiB", flush=True)
    return out


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30_000
    ensure_layout()
    from tests.support.curation_fixtures import run_operator

    t = table(n)
    plain = {
        "split_names": ["train", "test"],
        "split_fractions": [0.8, 0.2],
        "held_out": [],
        "stratify_by": ["label"],
        "group_column": "carrier_id",
    }
    print(f"rows: {n:,}")
    timed("split@1.0.0 (executor)", lambda: run_operator("split", plain, t, ROLES, version="1.0.0"))
    timed(
        "split@1.1.0, no new parameter (executor)",
        lambda: run_operator("split", plain, t, ROLES, version="1.1.0"),
    )
    work = Path(tempfile.mkdtemp(prefix="bench017-files-"))
    for name, params in CONFIGS.items():
        ran = timed(
            f"split@1.1.0 directed, {name} (executor; components + proof)",
            lambda p=params: run_operator("split", p, t, ROLES, version="1.1.0"),
        )
        out = ran.output
        names = out.column("_dw_split").to_pylist()
        paths: dict[str, Path] = {}
        for split in sorted(set(names)):
            path = work / f"{name}-{split}.parquet"
            pq.write_table(out.filter(pa.array([s == split for s in names])), path)
            paths[split] = path
        held = set(params["held_out"])
        training = {s: p for s, p in paths.items() if s not in held}
        roles = ran.meta["split_roles"]

        def prove_all(
            r: dict[str, Any] = roles,
            h: set[str] = held,
            paths: dict[str, Path] = paths,
            training: dict[str, Path] = training,
        ) -> list[bool]:
            return [
                prove_files(
                    s,
                    DirectedBy.from_dict(r[s]["directed_by"]),
                    [paths[s]],
                    training,
                ).passes
                for s in sorted(h)
                if s in paths
            ]

        passes = timed(f"finalize proof over written files, {name}", prove_all)
        print(f"{'':<58} proofs pass: {passes}")
    print("census: not measured (017 slice 2)")


if __name__ == "__main__":
    main()
