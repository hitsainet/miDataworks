"""Benchmark the 020 lookups L1, L2, L3 before and after previous-address equivalence (FTASKS 6.15).

Run from ``backend/`` against a TEST database (its name must contain ``test``)::

    DATABASE_URL=postgresql+asyncpg://.../x_test python -m scripts.bench_020_lookups --marks 20000 --regs 20000

"Before" is the opt-in default: no previous origin listed, so every lookup is exactly the old
text match (``matching_values`` answers ``[url]`` with no query). "After" lists one previous
origin, so each lookup adds one ``SELECT DISTINCT`` of the address column. The table is seeded
with rows under the current, the previous and many unrelated addresses. Prints p50/p95 in ms; the
FTASKS budget is a p50 increase under 5 ms (over budget is a finding, not a pass).
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
import uuid

NEW = "http://miworkbench.bench:8000"
OLD = "http://mistudio.bench:8000"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--marks", type=int, default=20000)
    ap.add_argument("--regs", type=int, default=20000)
    ap.add_argument("--hosts", type=int, default=50, help="distinct unrelated addresses")
    ap.add_argument("--runs", type=int, default=200)
    ap.add_argument(
        "--distinct",
        action="store_true",
        help="measure the former SELECT DISTINCT lookup (for a before/after of the loose index scan)",
    )
    args = ap.parse_args()
    if "test" not in os.environ.get("DATABASE_URL", "").rsplit("/", 1)[-1]:
        print("refusing: DATABASE_URL must name a test database", file=sys.stderr)
        return 2

    from sqlalchemy import select, text

    from src.core import sibling_origins as so
    from src.core.config import get_settings
    from src.core.database import get_sync_engine, sync_session_factory
    from src.models.detector_send import MiStudioRegistration
    from src.services.detector_sets import reward_marks
    from tests.support import db_factories

    engine = get_sync_engine()
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE dw_reward_marks, dw_mistudio_registrations CASCADE"))
    with sync_session_factory()() as db:
        version_id = str(uuid.uuid4())
        db_factories.version(db, id=version_id)
        db.commit()
    hosts = [NEW, OLD] + [f"http://other{i}.bench:8000" for i in range(args.hosts)]
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO dw_reward_marks (id, mistudio_base_url, mistudio_probe_id, source, reason,"
                " marked_by, marked_by_origin) SELECT 'rwm_' || g, (:h)[1 + g % :n], 'pm_' || g,"
                " 'operator', 'bench', 'bench', 'operator' FROM generate_series(1, :c) g"
            ),
            {"h": hosts, "n": len(hosts), "c": args.marks},
        )
        conn.execute(
            text(
                "INSERT INTO dw_mistudio_registrations (id, kind, mistudio_base_url, split,"
                " mistudio_dataset_id, role, mapping_sha256, columns_sha256, probe_dataset_id,"
                " version_id) SELECT 'msr_' || g, 'view', (:h)[1 + g % :n], 'test', 'ds_' || g,"
                " 'train', repeat('0', 64), repeat('1', 64), 'pmd_' || g, CAST(:v AS uuid)"
                " FROM generate_series(1, :c) g"
            ),
            {"h": hosts, "n": len(hosts), "c": args.regs, "v": version_id},
        )
        conn.execute(text("ANALYZE dw_reward_marks; ANALYZE dw_mistudio_registrations"))

    settings = get_settings()
    settings.mistudio_base_url = NEW
    if args.distinct:
        so.distinct_values_statement = lambda column: select(column).distinct()  # type: ignore[assignment]

    def l1(db):  # type: ignore[no-untyped-def]
        reward_marks.marked_probe_ids(db, NEW)

    def l2(db):  # type: ignore[no-untyped-def]
        reward_marks.trace(
            db, base_url=NEW, probe_id="pm_x", train_view_id="pmd_7", model_id="m", layer=1
        )

    def l3(db):  # type: ignore[no-untyped-def]
        db.execute(
            select(MiStudioRegistration)
            .where(
                MiStudioRegistration.kind == "view",
                MiStudioRegistration.mistudio_base_url.in_(
                    so.matching_values(
                        db, MiStudioRegistration.mistudio_base_url, so.Sibling.MIWORKBENCH, NEW
                    )
                ),
                MiStudioRegistration.probe_dataset_id == "pmd_9",
            )
            .order_by(so.prefer_exact(MiStudioRegistration.mistudio_base_url, NEW))
            .limit(1)
        ).scalar_one_or_none()

    print(f"rows: marks={args.marks} registrations={args.regs} distinct addresses={len(hosts)}")
    for name, fn in (("L1", l1), ("L2", l2), ("L3", l3)):
        out = {}
        for label, previous in (("before", ()), ("after", (OLD,))):
            settings.miworkbench_previous_base_urls = previous
            times = []
            with sync_session_factory()() as db:
                for _ in range(10):
                    fn(db)
                for _ in range(args.runs):
                    start = time.perf_counter()
                    fn(db)
                    times.append((time.perf_counter() - start) * 1000)
            times.sort()
            out[label] = (statistics.median(times), times[int(len(times) * 0.95) - 1])
        delta = out["after"][0] - out["before"][0]
        print(
            f"{name}: before p50 {out['before'][0]:.2f} ms p95 {out['before'][1]:.2f} ms | "
            f"after p50 {out['after'][0]:.2f} ms p95 {out['after'][1]:.2f} ms | "
            f"p50 increase {delta:.2f} ms ({'within' if delta < 5 else 'OVER'} the 5 ms budget)"
        )
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE dw_reward_marks, dw_mistudio_registrations CASCADE"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
