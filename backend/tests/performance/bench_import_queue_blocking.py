"""T-04: how long does a curation task wait while imports run on ``curation``? (001 FTASKS 14.4)

Run by hand (not collected): ``python tests/performance/bench_import_queue_blocking.py``. It starts a
real Celery worker on the ``curation`` queue with the production settings that decide queueing
(``CELERY_CONCURRENCY=2`` from ``k8s/base/backend.yaml``, prefork, ``acks_late``, prefetch 1) on a
private Redis database, then measures the wait of a trivial curation task while 0, 1 and 2 long
"imports" (a task that sleeps ``IMPORT_S`` seconds, standing in for a download) occupy the queue.

The wait is the time from ``apply_async`` to the trivial task STARTING (it records its own start).
"""

from __future__ import annotations

import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from celery import Celery

BROKER = os.environ.get("BENCH_REDIS", "redis://127.0.0.1:56380/9")
CONCURRENCY = 2  # k8s/base/backend.yaml worker-curation CELERY_CONCURRENCY
IMPORT_S = float(os.environ.get("BENCH_IMPORT_S", "20"))
SAMPLES = 3

app = Celery("bench", broker=BROKER, backend=BROKER)
app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_queue="curation",
    result_expires=300,
)


@app.task(name="bench.import")
def fake_import(seconds: float) -> float:
    time.sleep(seconds)
    return seconds


@app.task(name="bench.curation")
def trivial() -> float:
    return time.time()


def worker() -> subprocess.Popen[bytes]:
    here = Path(__file__).resolve()
    env = dict(os.environ, PYTHONPATH=str(here.parent))
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            here.stem,
            "worker",
            "-Q",
            "curation",
            "--pool",
            "prefork",
            "--concurrency",
            str(CONCURRENCY),
            "--loglevel",
            "WARNING",
            "--without-gossip",
            "--without-mingle",
        ],
        cwd=here.parent,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(60):
        if app.control.inspect(timeout=1).ping():
            return proc
    proc.kill()
    raise SystemExit("worker did not start")


def wait_with(imports: int) -> list[float]:
    waits = []
    for _ in range(SAMPLES):
        running = [fake_import.apply_async((IMPORT_S,)) for _ in range(imports)]
        time.sleep(1.0)  # let the worker pick the imports up first
        sent = time.time()
        started = trivial.apply_async().get(timeout=IMPORT_S * 3 + 30)
        waits.append(started - sent)
        for r in running:
            r.get(timeout=IMPORT_S * 3 + 30)
    return waits


def main() -> None:
    app.control.purge()
    proc = worker()
    try:
        print(f"concurrency={CONCURRENCY} import_s={IMPORT_S} samples={SAMPLES}")
        for n in (0, 1, 2):
            waits = wait_with(n)
            print(
                f"imports running={n}: curation wait median {statistics.median(waits):.2f} s, "
                f"max {max(waits):.2f} s"
            )
    finally:
        proc.terminate()
        proc.wait(timeout=30)


if __name__ == "__main__":
    tempfile.tempdir = None
    main()
