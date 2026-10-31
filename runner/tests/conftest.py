"""Fixtures for the runner tests: the backend src on the path, the bundle, a real runner process.

The bundle comes from ``RUNNER_BUNDLE_DIR`` (built by ``runner/scripts/build_bundle.py``). When it is
absent or its snapshot does not match the packaged contract's digest, the runner tests skip loudly,
and fail instead under ``MIDATAWORKS_REQUIRE_RUNNER=1`` (set by the ``runner-tests`` CI job). The real
runner is started as a SUBPROCESS whose environment holds only ``RUNNER_*`` and ``PATH`` — no database
URL, no secret — so a runner that silently needed one would fail to answer.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

RUNNER = Path(__file__).resolve().parents[1]
REPO = RUNNER.parent
BACKEND = REPO / "backend"
sys.path.insert(0, str(BACKEND))

PACKAGED_CONTRACT = BACKEND / "src" / "schemas" / "data" / "midataworks-runner-v1.json"
REQUIRE = os.environ.get("MIDATAWORKS_REQUIRE_RUNNER") == "1"


def _skip(message: str) -> None:
    if REQUIRE:
        pytest.fail("RUNNER: " + message)
    pytest.skip("RUNNER: " + message)


def _bundle_dir() -> Path:
    env = os.environ.get("RUNNER_BUNDLE_DIR")
    if not env:
        _skip("RUNNER_BUNDLE_DIR is not set; build the bundle with runner/scripts/build_bundle.py")
    return Path(env)


def _check_bundle(bundle_dir: Path) -> Path:
    snapshot = bundle_dir / "snapshot.wasm"
    if not snapshot.is_file():
        _skip(f"no snapshot.wasm in {bundle_dir}")
    contract = json.loads(PACKAGED_CONTRACT.read_text(encoding="utf-8"))
    want = contract["guest"]["bundle"]["snapshot"]["sha256"]
    got = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    if got != want:
        _skip(f"snapshot.wasm digest {got} does not match the contract's {want}")
    return snapshot


@pytest.fixture(scope="session")
def bundle_dir() -> Path:
    directory = _bundle_dir()
    _check_bundle(directory)
    return directory


@pytest.fixture(scope="session")
def contract() -> dict[str, Any]:
    return json.loads(PACKAGED_CONTRACT.read_text(encoding="utf-8"))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class RunnerProcess:
    def __init__(self, base_url: str, env: dict[str, str]) -> None:
        self.base_url = base_url
        self.env = env

    def health(self) -> dict[str, Any]:
        import httpx

        return httpx.get(self.base_url + "/health", timeout=10).json()

    def run(self, body: dict[str, Any]) -> Any:
        import httpx

        return httpx.post(self.base_url + "/v1/run", json=body, timeout=30)

    def inspect(self, body: dict[str, Any]) -> Any:
        import httpx

        return httpx.post(self.base_url + "/v1/inspect", json=body, timeout=30)


def runner_env(bundle_dir: Path, port: int) -> dict[str, str]:
    """Deliberately minimal: PATH, PYTHONPATH and RUNNER_* only. No DATABASE_URL, REDIS_URL or any
    secret, and nothing inherited from the calling process (the process barrier, FR-018.24)."""
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "RUNNER_BUNDLE_DIR": str(bundle_dir),
        "RUNNER_CONTRACT_PATH": str(PACKAGED_CONTRACT),
        "RUNNER_HOST": "127.0.0.1",
        "RUNNER_PORT": str(port),
        "PYTHONPATH": str(BACKEND),
    }


class StartedRunner(RunnerProcess):
    def __init__(self, base_url: str, env: dict[str, str], proc: subprocess.Popen[bytes]) -> None:
        super().__init__(base_url, env)
        self.proc = proc
        self.pid = proc.pid


@contextlib.contextmanager
def start_runner(bundle_dir: Path, stderr_path: Path | None = None) -> Iterator[StartedRunner]:
    """A real runner as a SUBPROCESS on a free port; stderr to ``stderr_path`` when given."""
    import httpx

    port = _free_port()
    env = runner_env(bundle_dir, port)
    err_file = stderr_path.open("wb") if stderr_path is not None else subprocess.DEVNULL
    proc = subprocess.Popen(
        [sys.executable, "-m", "src.runner.server"],
        env=env,
        cwd=str(BACKEND),
        stdout=subprocess.DEVNULL,
        stderr=err_file,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"runner exited early with {proc.returncode}")
            try:
                if httpx.get(base_url + "/health", timeout=2).status_code in (200, 503):
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        else:
            raise RuntimeError("runner did not become ready")
        yield StartedRunner(base_url, env, proc)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        if stderr_path is not None:
            err_file.close()  # type: ignore[union-attr]


@pytest.fixture(scope="session")
def runner_process(bundle_dir: Path) -> Iterator[RunnerProcess]:
    with start_runner(bundle_dir) as runner:
        yield runner


@pytest.fixture
def runner_starter() -> Any:
    """:func:`start_runner` for a test that needs its own runner process (barrier, log guard)."""
    return start_runner
