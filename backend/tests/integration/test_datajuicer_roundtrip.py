"""The ping task round-trips a Parquet file through the ``datajuicer`` queue (task 10.4).

A worker started from the runner module (as the Data-Juicer image starts it) consumes only that
queue. The backend's Celery app sends the task BY NAME, as it will in production, and its routing
table delivers it to ``datajuicer``. Data-Juicer itself is not installed here; the shell's ping
needs only Celery and pyarrow.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import redis

from tests.support.live_stack import BACKEND, private_redis_url

BIN = Path(sys.executable).parent


def test_ping_round_trips_through_the_datajuicer_queue(tmp_path: Path) -> None:
    from celery import Celery

    redis_url = private_redis_url()
    redis.Redis.from_url(redis_url).flushdb()
    data = tmp_path / "data"
    (data / "runs" / "job_dj").mkdir(parents=True)
    source = data / "runs" / "job_dj" / "in.parquet"
    pq.write_table(
        pa.table({"row_id": list(range(40)), "text": [f"r{i}" for i in range(40)]}), source
    )
    out = data / "runs" / "job_dj" / "out.parquet"

    env = {**os.environ, "REDIS_URL": redis_url, "DATA_DIR": str(data), "PYTHONUNBUFFERED": "1"}
    log = open(tmp_path / "dj.log", "w")  # noqa: SIM115
    worker = subprocess.Popen(
        [
            str(BIN / "celery"),
            "-A",
            "src.operators.datajuicer.runner",
            "worker",
            "-Q",
            "datajuicer",
            "-P",
            "solo",
            "-n",
            f"dj-{uuid.uuid4().hex[:6]}@%h",
            "--without-gossip",
            "--without-mingle",
            "-l",
            "INFO",
        ],
        cwd=BACKEND,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 60
        while "ready" not in (tmp_path / "dj.log").read_text():
            assert time.monotonic() < deadline, (tmp_path / "dj.log").read_text()
            time.sleep(0.2)
        from src.core.celery_app import TASK_ROUTES

        sender = Celery("sender", broker=redis_url, backend=redis_url)
        sender.conf.task_routes = TASK_ROUTES
        result = sender.send_task("midataworks.datajuicer.ping", args=[str(source), str(out)])
        assert result.get(timeout=30) == {"rows": 40, "output_path": str(out)}
        assert pq.read_table(out).num_rows == 40
        assert pq.read_table(out).column("text").to_pylist() == [f"r{i}" for i in range(40)]
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=15)
        except subprocess.TimeoutExpired:
            worker.kill()
        log.close()


@pytest.mark.parametrize("queue", ["default", "curation"])
def test_the_backend_never_routes_ping_elsewhere(queue: str) -> None:
    from src.core.celery_app import route_for

    assert route_for("midataworks.datajuicer.ping") != queue
