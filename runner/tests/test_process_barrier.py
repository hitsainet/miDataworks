"""The process barrier (feature 018; FR-018.24; FTDD §9.2 barrier 2; FTASKS 6.8; M10).

The guest barrier (WebAssembly, Table W) is one wall; the PROCESS is the second: the runner is its
own process, started with its own minimal environment, holding nothing of its caller's. A sentinel
the calling process holds in its environment - standing for DATABASE_URL, a token, any secret of
the backend - must not exist in the runner process at all: not in ``/proc/<pid>/environ``, not in
``/health`` ``env_names`` (the live half of Property 1). M10 (the runner started inheriting its
caller's environment, i.e. the guest run "in the calling process") turns this red.

In the cluster the same property is a separate pod with literal env only
(``backend/tests/unit/test_k8s_manifests.py::TestRunner``); this is its process-level proof.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any

import pytest

from src.clients import runner_client as rc
from src.runner_contract.cases import load_cases

#: What the runner was started with, plus LC_CTYPE, which CPython sets in its own environment
#: when it coerces the C locale (PEP 538) - the interpreter's, not the caller's.
ALLOWED_NAMES = {"PATH", "PYTHONPATH", "LC_CTYPE"}


def test_the_runner_holds_nothing_of_its_callers_environment(
    bundle_dir: Path, monkeypatch: pytest.MonkeyPatch, runner_starter: Any
) -> None:
    name = "MIDATAWORKS_BARRIER_SENTINEL"
    value = "sentinel-" + secrets.token_hex(16)
    monkeypatch.setenv(name, value)
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:" + value + "@db/x")
    with runner_starter(bundle_dir) as runner:
        assert runner.pid != os.getpid()
        environ = Path(f"/proc/{runner.pid}/environ").read_bytes()
        assert value.encode() not in environ
        assert name.encode() not in environ and b"DATABASE_URL" not in environ
        names = runner.health()["env_names"]
        assert name not in names and "DATABASE_URL" not in names
        assert set(names) <= ALLOWED_NAMES | {n for n in names if n.startswith("RUNNER_")}
        # And it is a working runner, not an empty process: a vector runs through it.
        doc = load_cases()["documents"]["count_tokens"]
        answer = rc.RunnerClient(runner.base_url).run(
            rc.DocumentRef.of(doc),
            [rc.RunCall("f", {}, ["one two three"])],
            mode="stored",
            lane="batch",
            limits=_floor_limits(),
            send_document=True,
        )
        assert answer.results == [[{"count": 3}]]


def _floor_limits() -> dict[str, int]:
    import json

    contract = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "backend/src/schemas/data/midataworks-runner-v1.json"
        ).read_text(encoding="utf-8")
    )
    floor = contract["limits_floor"]
    limits = {k: floor[k] for k in contract["request_limits"] if k in floor}
    limits["wall_ms"] = 10_000
    return limits
