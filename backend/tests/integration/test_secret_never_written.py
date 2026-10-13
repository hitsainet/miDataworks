"""Stored secrets never reach a row, a result, a file or a log (ADR-015; task 8.6).

The self-test job runs with a known Hugging Face token and endpoint keys configured, and with
``check_hf_token`` so the worker really decrypts the token. Then every ``dw_*`` table (discovered
from the live metadata, so feature 005's label-run and lease tables are covered the day they are
added), the job result, every file under ``DATA_DIR`` and every captured log record are searched
for each secret value.

Mutation controls: put the token into the job result; write it to a file under DATA_DIR; log it
with redaction disabled. Each must turn this file red.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.core.database import Base, get_sync_engine
from src.workers import selftest_tasks
from tests.support.source_fixtures import HfEnv, hf_env

__all__ = ["hf_env"]

HF_TOKEN = "hf_NeverWriteMeAnywhere_0123456789abcdef"
JUDGE_KEY = "sk-judge-NeverWriteMe-0123456789"
CLASSIFIER_KEY = "tei-key-NeverWriteMe-0123456789"
SECRETS = (HF_TOKEN, JUDGE_KEY, CLASSIFIER_KEY)


def _all_rows_as_text() -> str:
    chunks: list[str] = []
    with get_sync_engine().connect() as conn:
        for table in Base.metadata.sorted_tables:
            rows = conn.execute(text(f"SELECT t::text FROM {table.name} AS t")).scalars()
            chunks.extend(rows)
    return "\n".join(chunks)


def _all_files_as_bytes(root: Path) -> bytes:
    return b"\n".join(p.read_bytes() for p in root.rglob("*") if p.is_file())


async def test_secrets_appear_nowhere_after_a_job_runs(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.DEBUG)
    emitted: list[dict[str, Any]] = []
    monkeypatch.setattr(selftest_tasks, "emit", lambda room, event, data: emitted.append(data))
    from src.core.celery_app import celery_app

    monkeypatch.setattr(celery_app, "send_task", lambda *a, **k: None)

    assert (
        await client.put("/api/v1/settings/hf_token", json={"value": HF_TOKEN})
    ).status_code == 200
    await client.put(
        "/api/v1/endpoint-roles/judge",
        json={"protocol": "openai_chat", "base_url": "http://judge.test/v1", "api_key": JUDGE_KEY},
    )
    await client.put(
        "/api/v1/endpoint-roles/classifier",
        json={
            "protocol": "tei_classification",
            "base_url": "http://tei.test",
            "api_key": CLASSIFIER_KEY,
        },
    )
    created = await client.post(
        "/api/v1/jobs/selftest",
        json={"duration_seconds": 0.3, "step_seconds": 0.1, "rows": 50, "check_hf_token": True},
    )
    job_id = created.json()["id"]

    result = selftest_tasks.run_selftest.run(job_id)  # the task body, in this process
    assert result["status"] == "completed"
    assert result["hf_token_configured"] is True, "the worker must really have decrypted the token"

    job = (await client.get(f"/api/v1/jobs/{job_id}")).json()
    rows = _all_rows_as_text()
    files = _all_files_as_bytes(data_dir)
    responses = (await client.get("/api/v1/settings")).text + (
        await client.get("/api/v1/endpoint-roles")
    ).text
    for secret in SECRETS:
        assert secret not in rows, f"{secret[:6]}… is stored in clear in a dw_* row"
        assert secret not in str(job), f"{secret[:6]}… is in the job record or result"
        assert secret not in str(result), f"{secret[:6]}… is in the task result"
        assert secret.encode() not in files, f"{secret[:6]}… is in a file under DATA_DIR"
        assert secret not in caplog.text, f"{secret[:6]}… reached a log record"
        assert secret not in str(emitted), f"{secret[:6]}… was emitted over the socket"
        assert secret not in responses, f"{secret[:6]}… was returned by the API"
    assert job["status"] == "completed"


# --------------------------------------------------------------------------------------------
# Feature 001 (FTASKS 13.2): a per-import token and the stored token through preview, an
# operator import and an approved agent import.
# --------------------------------------------------------------------------------------------

PER_IMPORT = "hf_PerImportNeverWritten_9876543210fedcba"


async def test_hf_tokens_appear_nowhere_after_preview_import_and_agent_import(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    caplog: pytest.LogCaptureFixture,
    hf_env: HfEnv,
) -> None:
    from src.core import ephemeral_secrets
    from tests.support.hf_mock import COLBERT, HUMICROEDIT

    caplog.set_level(logging.DEBUG)
    env = hf_env
    redis = ephemeral_secrets.redis_client()
    for key in redis.scan_iter(match=ephemeral_secrets.PREFIX + "*"):  # earlier tests' leftovers
        redis.delete(key)
    assert (
        await client.put("/api/v1/settings/hf_token", json={"value": HF_TOKEN})
    ).status_code == 200

    preview = await client.post(
        "/api/v1/sources/hf/preview", json={"repo_id": COLBERT, "access_token": PER_IMPORT}
    )
    assert preview.status_code == 200, preview.text
    stored = await client.post("/api/v1/sources/hf", json={"repo_id": COLBERT})  # stored tier
    agent = await client.post(
        "/api/v1/sources/hf",
        json={"repo_id": HUMICROEDIT, "config": "subtask-1", "access_token": PER_IMPORT},
        headers={"X-Dataworks-Agent": "agent:mcp"},
    )
    assert stored.status_code == 202 and agent.status_code == 202
    approved = await client.post(f"/api/v1/approvals/{agent.json()['approval_id']}/approve")
    assert approved.json()["status"] == "executed", approved.text
    results = env.run_imports()
    assert len(results) == 2
    tokens_seen = {c.get("token") for c in env.loader.calls}
    assert HF_TOKEN in tokens_seen and PER_IMPORT in tokens_seen, "both tiers really reached HF"

    jobs = [(await client.get(f"/api/v1/jobs/{r['job_id']}")).text for r in [stored.json()]]
    rows = _all_rows_as_text()
    files = _all_files_as_bytes(data_dir)
    leftovers = [
        ephemeral_secrets.redis_client().get(k)
        for k in ephemeral_secrets.redis_client().scan_iter(match=ephemeral_secrets.PREFIX + "*")
    ]
    surfaces = {
        "a dw_* row": rows,
        "a job record": " ".join(jobs),
        "a task result": str(results),
        "the preview result": preview.text,
        "a Celery message": str(env.sent),
        "a Socket.IO payload": str(env.emitted),
        "a log record": caplog.text,
        "an API response": stored.text + agent.text + approved.text,
    }
    for secret in (HF_TOKEN, PER_IMPORT):
        for where, haystack in surfaces.items():
            assert secret not in haystack, f"{secret[:8]}… is in {where}"
        assert secret.encode() not in files, f"{secret[:8]}… is in a file under DATA_DIR"
        assert all(secret.encode() not in (v or b"") for v in leftovers)
    keys = list(ephemeral_secrets.redis_client().scan_iter(match=ephemeral_secrets.PREFIX + "*"))
    assert leftovers == [], f"every per-import token was taken (GETDEL) by its worker: {keys}"


async def test_a_relay_step_leaks_the_key_nowhere(
    client: httpx.AsyncClient,
    data_dir: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """003 FTASKS 8.4: a model-calling step through the loopback relay (the Data Designer path
    and its T-32 fallback). The real key is absent from rows, results, files, logs, the
    environment and the relay records afterwards; the nonce variable is gone too."""
    import os

    from src.operators import endpoint_port, executor
    from src.operators.endpoint_port import ResolvedEndpoint
    from tests.support import operator_fixtures as fx
    from tests.support.fake_openai import FakeOpenAI

    caplog.set_level(logging.DEBUG)
    with FakeOpenAI() as upstream:

        class Resolver:
            def resolve(self, role: str) -> ResolvedEndpoint:
                return ResolvedEndpoint(role, upstream.base_url, "m1", JUDGE_KEY, True)

        monkeypatch.setattr(endpoint_port, "_resolver", Resolver())
        reg = fx.registry()
        spec = fx.stage_spec(reg, "fx_relay_echo", data=fx.table(["first row", "second row"]))
        result = executor.execute_in_process(spec, registry=reg)
        assert len(upstream.requests) == 2
        assert upstream.requests[0]["headers"]["authorization"] == f"Bearer {JUDGE_KEY}"
    assert [r["row_key"] for r in result.relay_records] == [
        fx.table(["first row"]).column("_dw_row_key")[0].as_py(),
        fx.table(["second row"]).column("_dw_row_key")[0].as_py(),
    ]
    haystacks = {
        "rows": _all_rows_as_text(),
        "files": _all_files_as_bytes(data_dir).decode("utf-8", "replace"),
        "logs": "\n".join(r.getMessage() for r in caplog.records),
        "environment": "\n".join(f"{k}={v}" for k, v in os.environ.items()),
        "records": repr(result.relay_records),
    }
    for where, haystack in haystacks.items():
        assert JUDGE_KEY not in haystack, f"the key leaked into {where}"
    assert "DW_RELAY_NONCE" not in os.environ


async def test_a_generation_run_a_preview_and_a_diversity_report_leak_no_key(
    client: httpx.AsyncClient,
    data_dir: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """007 FTASKS 14.4: the generation and embeddings keys and the lease id reach no row, job
    result, file, log or API response, on both call paths."""
    from contextlib import ExitStack

    from tests.support import generation_fixtures as g

    caplog.set_level(logging.DEBUG)
    gen_key, embed_key = "sk-gen-NeverWriteMe-0123456789", "sk-embed-NeverWriteMe-0123456789"
    with ExitStack() as stack:
        state = stack.enter_context(_gen_state(monkeypatch, data_dir))
        g.set_role("generation", state.base_url, g.GEN_MODEL, api_key=gen_key)
        g.set_role(
            "embeddings",
            state.base_url,
            "embed-model",
            protocol="openai_embeddings",
            api_key=embed_key,
        )
        responses: list[str] = []
        template = await g.respond_template(client)
        from src.core.config import get_settings

        for path in ("native", "relay"):
            monkeypatch.setattr(get_settings(), "generation_engine_path", path)
            parent = g.make_version(g.version_table(["one", "two"]))
            started = await client.post(
                "/api/v1/generation-runs", json=g.run_body(parent, template, sample_size=2)
            )
            responses.append(started.text)
            assert state.run_until_done(started.json()["id"]).state == "completed"
            responses.append(
                (await client.get(f"/api/v1/generation-runs/{started.json()['id']}/records")).text
            )
        preview = await client.post("/api/v1/generation-runs/preview", json={"prompts": ["hi"]})
        responses.append(preview.text)
        child = g.make_version(g.version_table(["one", "two"], generated=["gen"]), parent=parent)
        from src.workers import diversity_tasks

        requested = await client.post(f"/api/v1/versions/{child}/diversity", json={})
        diversity_tasks.report_job(requested.json()["job_id"])
        responses.append((await client.get(f"/api/v1/versions/{child}/diversity")).text)
        leases = [
            r.headers.get("x-millm-lease")
            for r in state.fake.requests
            if r.headers.get("x-millm-lease")
        ]
        assert leases and all(
            r.headers.get("authorization") == f"Bearer {gen_key}" for r in state.chats()
        )
    haystacks = {
        "rows": _all_rows_as_text(),
        "files": _all_files_as_bytes(data_dir).decode("utf-8", "replace"),
        "logs": "\n".join(r.getMessage() for r in caplog.records),
        "responses": "\n".join(responses),
    }
    for secret in (gen_key, embed_key, *set(leases)):
        for where, haystack in haystacks.items():
            assert secret not in haystack, f"{secret[:10]}… leaked into {where}"


def _gen_state(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> Any:
    """The ``gen`` fixture's body as a context manager (this module's tests use ``client``)."""
    from contextlib import contextmanager

    from tests.support import generation_fixtures as g

    @contextmanager
    def state() -> Any:
        from src.clients import endpoint_caller
        from src.core.celery_app import celery_app
        from src.operators import endpoint_port
        from src.services import labeling_ports
        from src.workers import generation_tasks

        previous = endpoint_caller.install_transport(None)
        resolver, leases = endpoint_port.resolver(), endpoint_port.lease_manager()
        labeling_ports.install()
        fake = g.default_fake()
        with g.FakeMillmServer(fake) as server:
            st = g.Gen(fake, server, data_dir)
            g.set_operator()
            g.set_role("judge", st.base_url, g.JUDGE_MODEL)
            monkeypatch.setattr(celery_app, "send_task", st.send_task)
            monkeypatch.setattr(generation_tasks, "_engine", st.engine)
            monkeypatch.setattr(generation_tasks, "_next_jobs", lambda: None)
            try:
                yield st
            finally:
                endpoint_caller.install_transport(previous)
                endpoint_port.install_resolver(resolver)
                endpoint_port.install_lease_manager(leases)

    return state()
