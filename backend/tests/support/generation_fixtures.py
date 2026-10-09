"""Fixtures for feature 007: a fake miLLM on a REAL loopback port, versions with a held-out split,
roles pointed at the fake, and an in-process job runner (FTID 007 section 8).

The fake (``fake_millm.FakeMillm`` with ``generation_mode``) is served over HTTP on ``127.0.0.1``
so BOTH generation paths reach it the way production reaches miLLM: the native path through 005's
``EndpointCaller`` and the relay path through 003's loopback relay. No transport is injected
anywhere; every request is recorded with its headers and body, so tests assert payload and count.
"""

from __future__ import annotations

import socket
import threading
import time
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import uvicorn
from sqlalchemy import select
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from src.core.database import sync_session_factory
from src.core.encryption import encrypt_value
from src.models.app_setting import AppSetting
from src.models.endpoint_role import EndpointRole
from src.models.generation import GenerationRun, GenerationRunJob
from src.models.job import Job
from src.services.row_keys import compute_row_key
from tests.support import db_factories
from tests.support.fake_millm import FakeMillm

GEN_MODEL = "gen-model-7b"
JUDGE_MODEL = "judge-model-9b"
REVISION = "4f2c0d1e8b7a6c5d4e3f2a1b0c9d8e7f6a5b4c3d"
SAE = "LiquidAI--LFM2.5-1.2B-Instruct-sae--layer_11"
KEY = "sk-test-generation-key-0123456789"
PASSTHROUGH = ("content-type", "retry-after")


class FakeMillmServer:
    """``with FakeMillmServer(fake) as server: server.base_url`` — real HTTP on loopback."""

    def __init__(self, fake: FakeMillm) -> None:
        self.fake = fake
        self.base_url = ""
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    def app(self) -> Starlette:
        async def handle(request: Request) -> Response:
            body = await request.body()
            forwarded = httpx.Request(
                request.method,
                str(request.url),
                headers=[(k.decode(), v.decode()) for k, v in request.headers.raw],
                content=body,
            )
            import anyio.to_thread

            answer = await anyio.to_thread.run_sync(self.fake.handle, forwarded)
            headers = {
                k: v
                for k, v in answer.headers.items()
                if k.lower() in PASSTHROUGH or k.lower().startswith("x-millm-")
            }
            return Response(answer.content, status_code=answer.status_code, headers=headers)

        return Starlette(
            routes=[
                Route("/{path:path}", handle, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
            ]
        )

    def __enter__(self) -> FakeMillmServer:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        config = uvicorn.Config(self.app(), log_level="warning", access_log=False, lifespan="off")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run, kwargs={"sockets": [sock]}, daemon=True
        )
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            assert time.monotonic() < deadline, "the fake miLLM did not start"
            time.sleep(0.01)
        self.base_url = f"http://127.0.0.1:{port}"
        return self

    def __exit__(self, *_: Any) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10)


def set_role(
    role: str,
    base_url: str,
    model: str,
    *,
    protocol: str | None = "openai_chat",
    api_key: str | None = KEY,
    inherit: bool = False,
) -> None:
    with sync_session_factory()() as db:
        row = db.get(EndpointRole, role) or EndpointRole(role=role)
        row.protocol = protocol
        row.base_url = base_url
        row.model_id = model
        row.api_key_ciphertext = encrypt_value(api_key) if api_key else None
        row.use_mode = "own"
        row.inherit_from_judge = inherit
        db.merge(row)
        db.commit()


def set_operator(name: str = "Test Operator") -> None:
    with sync_session_factory()() as db:
        row = db.get(AppSetting, "operator_name")
        if row is None:
            db.add(AppSetting(key="operator_name", value=name))
        else:
            row.value = name
        db.commit()


ROLES = {"prompt": "content", "completion": "content"}


def version_table(
    train: Sequence[str],
    test: Sequence[str] = ("held-out question one", "held-out two"),
    *,
    extra: dict[str, Sequence[Any]] | None = None,
    generated: Sequence[str] = (),
) -> pa.Table:
    """Rows with every system column, keyed the way 002 keys them (content: prompt+completion)."""
    rows: list[dict[str, Any]] = []
    for split, texts, origin in (
        ("train", train, "source"),
        ("test", test, "source"),
        ("train", generated, "generated"),
    ):
        for i, text in enumerate(texts):
            row: dict[str, Any] = {"prompt": text, "completion": f"answer {i} to {text}"}
            row["_dw_row_key"] = compute_row_key(row, sorted(ROLES))
            row["_dw_occurrence"] = 0
            row["_dw_split"] = split
            row["_dw_origin"] = origin
            row["_dw_source_id"] = None
            row["_dw_source_locator"] = None
            row["_dw_parent_keys"] = None
            rows.append(row)
    schema = pa.schema(
        [
            ("prompt", pa.string()),
            ("completion", pa.string()),
            ("_dw_row_key", pa.string()),
            ("_dw_occurrence", pa.int32()),
            ("_dw_split", pa.string()),
            ("_dw_origin", pa.string()),
            ("_dw_source_id", pa.string()),
            ("_dw_source_locator", pa.string()),
            ("_dw_parent_keys", pa.list_(pa.string())),
        ]
    )
    table = pa.Table.from_pylist(rows, schema=schema)
    for name, values in (extra or {}).items():
        table = table.append_column(name, pa.array(list(values)))
    return table


def make_version(
    table: pa.Table,
    *,
    target_type: str = "sft",
    held_out: bool = True,
    roles: dict[str, str] | None = None,
    parent: str | None = None,
    bindings: list[dict[str, Any]] | None = None,
) -> str:
    """A completed version with one file per split; ``test`` is held out when ``held_out``."""
    import hashlib

    from src.core.storage import version_dir
    from src.models.version import VersionInput

    version_id = str(uuid.uuid4())
    directory = version_dir(version_id)
    directory.mkdir(parents=True, exist_ok=True)
    splits = []
    for name in sorted(set(table.column("_dw_split").to_pylist())):
        part = table.filter(pa.array([s == name for s in table.column("_dw_split").to_pylist()]))
        path = directory / f"{name}.parquet"
        pq.write_table(part, path)
        splits.append(
            {
                "name": name,
                "held_out": held_out and name == "test",
                "rows": part.num_rows,
                "bytes": path.stat().st_size,
                "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "logical_digest": "0" * 64,
                "path": f"versions/{version_id}/{name}.parquet",
            }
        )
    with sync_session_factory()() as db:
        ds = db_factories.dataset(db, target_type=target_type)
        db_factories.version(
            db,
            ds=ds,
            id=version_id,
            column_roles={**(roles or ROLES), "_dw_row_key": "system"},
            splits=splits,
            total_rows=table.num_rows,
            held_out_origin_version_id=version_id if held_out else None,
            parent_version_id=parent,
            bindings=bindings or [],
        )
        if parent is not None:
            db.add(
                VersionInput(
                    version_id=version_id, position=0, kind="version", input_version_id=parent
                )
            )
        db.commit()
    return version_id


@dataclass
class Gen:
    fake: FakeMillm
    server: FakeMillmServer
    data_dir: Path
    sent: list[tuple[str, list[Any], dict[str, Any]]] = field(default_factory=list)
    emitted: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    waits: list[float] = field(default_factory=list)
    deps_overrides: dict[str, Any] = field(default_factory=dict)

    @property
    def base_url(self) -> str:
        return self.server.base_url + "/v1"

    def send_task(self, name: str, args: list[Any] | None = None, **kwargs: Any) -> None:
        self.sent.append((name, list(args or []), kwargs))

    def emit(self, room: str, event: str, data: dict[str, Any]) -> bool:
        self.emitted.append((room, event, data))
        return True

    def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)

    def engine(self) -> Any:
        from src.services.generation.run_engine import EngineDeps, GenerationEngine

        return GenerationEngine(EngineDeps(emit=self.emit, sleep=self.sleep, **self.deps_overrides))

    def run_job(self, job_id: str) -> dict[str, Any]:
        from src.workers import generation_tasks

        return generation_tasks.run_job(job_id)

    def job_for(self, run_id: str, seq: int = -1) -> str:
        with sync_session_factory()() as db:
            links = list(
                db.execute(
                    select(GenerationRunJob)
                    .where(GenerationRunJob.run_id == run_id)
                    .order_by(GenerationRunJob.seq)
                ).scalars()
            )
            return links[seq].job_id

    def run_until_done(self, run_id: str) -> GenerationRun:
        self.run_job(self.job_for(run_id))
        return self.run(run_id)

    def run(self, run_id: str) -> GenerationRun:
        with sync_session_factory()() as db:
            row = db.get(GenerationRun, run_id)
            assert row is not None
            db.expunge(row)
            return row

    def job(self, job_id: str) -> Job:
        with sync_session_factory()() as db:
            row = db.get(Job, job_id)
            assert row is not None
            db.expunge(row)
            return row

    def chats(self) -> list[Any]:
        return [r for r in self.fake.requests if r.path == "/v1/chat/completions"]


def default_fake() -> FakeMillm:
    return FakeMillm(
        resident={
            "id": 7,
            "name": GEN_MODEL,
            "repo_id": "example/gen-model-7b",
            "revision": REVISION,
            "quantization": "FP16",
        },
        generation_mode=True,
        reports_steering=True,
        attachments=[{"sae_id": SAE, "layer": 11}],
    )


@pytest.fixture
def gen(monkeypatch: pytest.MonkeyPatch, data_dir: Path, clean_db: None) -> Iterator[Gen]:
    from src.clients import endpoint_caller
    from src.core.celery_app import celery_app
    from src.operators import endpoint_port
    from src.services import labeling_ports
    from src.workers import generation_tasks

    previous_transport = endpoint_caller.install_transport(None)
    previous_resolver = endpoint_port.resolver()
    previous_leases = endpoint_port.lease_manager()
    labeling_ports.install()
    fake = default_fake()
    with FakeMillmServer(fake) as server:
        state = Gen(fake, server, data_dir)
        set_operator()
        set_role("generation", state.base_url, GEN_MODEL)
        set_role("judge", state.base_url, JUDGE_MODEL)
        monkeypatch.setattr(celery_app, "send_task", state.send_task)
        monkeypatch.setattr(generation_tasks, "_engine", state.engine)
        monkeypatch.setattr(generation_tasks, "_next_jobs", lambda: None)
        try:
            yield state
        finally:
            endpoint_caller.install_transport(previous_transport)
            endpoint_port.install_resolver(previous_resolver)
            endpoint_port.install_lease_manager(previous_leases)


def run_body(version_id: str, template_id: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "input_version_id": version_id,
        "prompt_column": "prompt",
        "seed_splits": ["train"],
        "sample_size": 4,
        "seed": 11,
        "respond_template_id": template_id,
        "target_type": "sft",
    }
    body.update(overrides)
    return body


async def respond_template(client: httpx.AsyncClient) -> str:
    response = await client.get("/api/v1/generation-templates", params={"kind": "respond"})
    assert response.status_code == 200, response.text
    (builtin,) = [t for t in response.json()["items"] if t["name"] == "respond-v1"]
    return str(builtin["id"])


async def expand_template(client: httpx.AsyncClient) -> str:
    response = await client.get("/api/v1/generation-templates", params={"kind": "expand"})
    (builtin,) = [t for t in response.json()["items"] if t["name"] == "expand-v1"]
    return str(builtin["id"])


def texts(n: int, prefix: str = "seed question") -> list[str]:
    return [f"{prefix} {i:03d}" for i in range(n)]
