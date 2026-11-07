"""A real API server and a real Celery worker for integration tests (tasks 5.8, 6.4, 10.4).

Started as subprocesses against this test session's database, a private Redis database index
(per xdist worker, so two workers never consume each other's messages) and a private data
directory. Tests talk to the API over real HTTP and Socket.IO.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
import redis

BACKEND = Path(__file__).resolve().parents[2]
BIN = Path(sys.executable).parent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def private_redis_url() -> str:
    base = os.environ["REDIS_URL"].rsplit("/", 1)[0]
    worker = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
    index = 14 - int("".join(ch for ch in worker if ch.isdigit()) or 0)
    return f"{base}/{max(index, 1)}"


@dataclass
class Stack:
    api_url: str
    env: dict[str, str]
    data_dir: Path
    logs: Path

    def http(self) -> httpx.Client:
        return httpx.Client(base_url=self.api_url, timeout=10)


def _wait(predicate, timeout: float, what: str) -> None:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except Exception:  # noqa: BLE001 - not up yet
            pass
        time.sleep(0.2)
    raise TimeoutError(f"{what} did not come up within {timeout} s")


@contextmanager
def live_stack(
    tmp: Path, *, heartbeat_seconds: float = 1.0, queues: str = "default"
) -> Iterator[Stack]:
    port = free_port()
    redis_url = private_redis_url()
    redis.Redis.from_url(redis_url).flushdb()
    data_dir = tmp / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(
        REDIS_URL=redis_url,
        DATA_DIR=str(data_dir),
        DATAWORKS_API_URL=f"http://127.0.0.1:{port}",
        PROGRESS_HEARTBEAT_SECONDS=str(heartbeat_seconds),
        ENVIRONMENT="test",
        LOG_LEVEL="INFO",
        PYTHONUNBUFFERED="1",
    )
    api_log = open(tmp / "api.log", "w")  # noqa: SIM115
    worker_log = open(tmp / "worker.log", "w")  # noqa: SIM115
    api = subprocess.Popen(
        [str(BIN / "uvicorn"), "src.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=BACKEND,
        env=env,
        stdout=api_log,
        stderr=subprocess.STDOUT,
    )
    worker = subprocess.Popen(
        [
            str(BIN / "celery"),
            "-A",
            "src.core.celery_app:celery_app",
            "worker",
            "-Q",
            queues,
            "-P",
            "solo",
            "-n",
            f"test-{uuid.uuid4().hex[:8]}@%h",
            "--without-gossip",
            "--without-mingle",
            "-l",
            "INFO",
        ],
        cwd=BACKEND,
        env=env,
        stdout=worker_log,
        stderr=subprocess.STDOUT,
    )
    stack = Stack(f"http://127.0.0.1:{port}", env, data_dir, tmp)
    try:
        _wait(
            lambda: httpx.get(f"{stack.api_url}/api/health", timeout=2).status_code == 200,
            30,
            "API",
        )
        _wait(lambda: "ready" in (tmp / "worker.log").read_text(), 60, "Celery worker")
        yield stack
    finally:
        for process in (worker, api):
            process.terminate()
        for process in (worker, api):
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
        api_log.close()
        worker_log.close()


def wait_for_job(
    stack: Stack, job_id: str, statuses: set[str], timeout: float = 60
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    with stack.http() as http:
        while time.monotonic() < deadline:
            job = http.get(f"/api/v1/jobs/{job_id}").json()
            if job["status"] in statuses:
                return job  # type: ignore[no-any-return]
            time.sleep(0.2)
    logs = (stack.logs / "worker.log").read_text()[-3000:]
    raise TimeoutError(f"job {job_id} never reached {statuses}; worker log tail:\n{logs}")
