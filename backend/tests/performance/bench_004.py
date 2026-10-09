"""Feature 004 benchmarks on the changed paths (FTASKS 17.3; FTDD 004 §9.1). Run by hand, not
collected (no database needed):

    cd backend && python -m tests.performance.bench_004 [rows]

Each figure is measured through the code that ships: the audit through ``audit_table`` (10 metadata
columns), the sample profile through ``build_profile`` on 300 rows, exact dedup, MinHash dedup and
the split through 003's ``execute_in_process`` (per-batch effects and conservation included), and
the leakage check through ``leakage_service.check_table``. Peak RSS is reported per path.
"""

from __future__ import annotations

import os
import resource
import sys
import tempfile
import time
from typing import Any

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://midataworks:midataworks-dev@127.0.0.1:55433/midataworks_test_004",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:56380/7")
os.environ.setdefault("SETTINGS_ENCRYPTION_KEY", "bench-only-encryption-key-0123456789abcdef")
os.environ.setdefault("INTERNAL_API_SECRET", "bench-only-internal-secret-0123456789")
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="bench004-"))

import numpy as np  # noqa: E402
import pyarrow as pa  # noqa: E402

from src.core.storage import ensure_layout  # noqa: E402
from src.services.curation import leakage_service  # noqa: E402
from src.services.curation.audit_service import audit_table  # noqa: E402
from src.services.curation.profile_service import build_profile  # noqa: E402
from src.services.row_keys import ROWKEY_V1, compute_row_keys  # noqa: E402

WORDS = np.array([f"w{i}" for i in range(20_000)])


def table(n: int) -> tuple[pa.Table, dict[str, str]]:
    rng = np.random.default_rng(1)
    lengths = rng.integers(6, 20, size=n)
    texts = [
        f"{i} " + " ".join(WORDS[rng.integers(0, len(WORDS), size=k)])
        for i, k in enumerate(lengths)
    ]
    data: dict[str, Any] = {"text": texts, "label": rng.choice(["a", "b"], size=n).tolist()}
    roles = {"text": "content", "label": "metadata"}
    for c in range(10):
        data[f"m{c}"] = rng.integers(0, 3 + 7 * c, size=n).astype(str).tolist()
        roles[f"m{c}"] = "metadata"
    t = pa.table(data)
    keys = compute_row_keys(t, ["text"], ROWKEY_V1)
    t = t.append_column("_dw_row_key", pa.array(list(keys)))
    t = t.append_column("_dw_occurrence", pa.array(np.zeros(n, dtype=np.int32)))
    t = t.append_column("_dw_split", pa.array(np.where(rng.random(n) < 0.1, "test", "train")))
    return t, roles


def timed(label: str, fn: Any) -> Any:
    start = time.perf_counter()
    out = fn()
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(f"{label:<40} {time.perf_counter() - start:8.2f} s   peak RSS {rss:7.0f} MB", flush=True)
    return out


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
    ensure_layout()
    from tests.support.curation_fixtures import run_operator

    t, roles = timed(f"generate {n:,} rows", lambda: table(n))
    timed("shortcut audit, 10 metadata columns", lambda: audit_table(t, roles, "label", seed=1))
    sample = t.slice(0, 300)
    timed("sample profile, 300 rows", lambda: build_profile(sample, roles, sample=True, seed=1))
    timed("exact dedup (executor)", lambda: run_operator("dedup_exact", {}, t, roles))
    timed(
        "split 90/10 by label (executor)",
        lambda: run_operator(
            "split",
            {
                "split_names": ["train", "test"],
                "split_fractions": [0.9, 0.1],
                "stratify_by": ["label"],
            },
            t,
            roles,
        ),
    )
    timed(
        "MinHash dedup, 128 permutations (executor)",
        lambda: run_operator("dedup_minhash", {}, t, roles),
    )
    sided = t.append_column(leakage_service.SIDE, t.column("_dw_split"))
    params = leakage_service.leakage_params(None, None)
    timed(
        "leakage check (exact + near)",
        lambda: leakage_service.check_table(sided, ["text"], params, 1),
    )


if __name__ == "__main__":
    main()
