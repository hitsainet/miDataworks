"""The Data-Juicer worker entry point: shell only in Foundation (ADR-010; tasks 10.1, 10.2).

Runs ONLY in the ``hitsai/midataworks-datajuicer`` image, as a Celery worker consuming only the
``datajuicer`` queue (and later ``datajuicer_preview``). It exchanges rows with the backend as
Parquet paths on the shared data volume and imports nothing from ``src``: the standard library,
Celery, pyarrow and Data-Juicer only. ``tests/unit/test_datajuicer_runner_imports.py`` walks this
module's abstract syntax tree and fails on anything else, because this image has neither the
backend's dependencies nor its database credentials.

Feature 003 adds the adapter and the allowlisted catalogue; Foundation ships the ping task that
proves the round trip.

Start (inside the image)::

    celery -A src.operators.datajuicer.runner worker -Q datajuicer -n datajuicer@%h
"""

from __future__ import annotations

import os
from pathlib import Path

import pyarrow.parquet as pq
from celery import Celery

QUEUE = "datajuicer"
PING_TASK = "midataworks.datajuicer.ping"


def _data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "/data/dataworks")).resolve()


def _confined(path: str) -> Path:
    """Refuse a path outside the data volume (ADR-004, ADR-015)."""
    root = _data_dir()
    candidate = Path(path).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"{candidate} is outside the data directory {root}")
    return candidate


def ping_copy(input_path: str, output_path: str) -> int:
    """Copy every row from one Parquet file to another, staged then renamed. Returns the count."""
    source = _confined(input_path)
    destination = _confined(output_path)
    table = pq.read_table(source)
    staging = _data_dir() / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    staged = staging / f"dj-{os.getpid()}-{destination.name}"
    try:
        pq.write_table(table, staged)
        with open(staged, "rb") as handle:
            os.fsync(handle.fileno())
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged, destination)
    finally:
        if staged.exists():
            staged.unlink()
    return int(table.num_rows)


app = Celery(
    "midataworks-datajuicer",
    broker=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    backend=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
)
app.conf.update(
    task_default_queue=QUEUE,
    task_routes={"midataworks.datajuicer.*": {"queue": QUEUE}},
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
)


# shared=False: a shared task would also register in the BACKEND app whenever this module is
# imported there, and the backend would then believe it can run Data-Juicer work itself.
@app.task(name=PING_TASK, shared=False)
def ping(input_path: str, output_path: str) -> dict[str, object]:
    """Round-trip rows through the Data-Juicer container (task 10.2)."""
    return {"rows": ping_copy(input_path, output_path), "output_path": output_path}
