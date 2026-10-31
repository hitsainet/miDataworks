"""A failed preview is STORED and read back as ``{status: failed, error}`` (PII run G3/F-1).

2026-10-20: a ``dd_expression`` preview that Data Designer refused read ``running`` for 90 s and
then ``404 preview_not_found: it finished more than an hour ago or never existed``. The designer
task's failure was never stored: ``send_preview`` linked only on success. Now each runner preview
carries a ``link_error`` to ``midataworks.operators.preview_failed``.

These tests drive the REAL Celery machinery in-process: the errback signature ``send_preview``
attaches is applied exactly as Celery's ``_call_task_errbacks`` applies it from a worker that does
NOT have the task registered (the designer image): ``(task_id, *signature args)``, with the failed
task's result written to the result backend first. The poll then goes through the live route.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest

from src.api.v1.endpoints import operators as endpoint
from src.core.celery_app import celery_app
from src.operators import preview
from src.operators.registry import DD_CATALOGUE, OperatorRegistry
from src.workers import operator_tasks

DD_MESSAGE = "User provided prompt generation template is invalid."


def _fresh_id() -> str:
    """A preview id no earlier run stored: results live an hour in a Redis no test flushes, so a
    fixed id would let a stale answer pass for a fresh one."""
    return uuid.uuid4().hex * 2


def _dd_entry() -> Any:
    reg = OperatorRegistry.build(
        native=(), catalogues=((DD_CATALOGUE, "data_designer"),), entry_points=()
    )
    return reg.entry("dd_expression", reg.current_version("dd_expression") or "")


def _request(preview_id: str) -> dict[str, Any]:
    entry = _dd_entry()
    return {
        "operator": "dd_expression",
        "version": entry.version,
        "params": {"expr": "{% set x = 1 %}{{ x }}"},
        "preview_id": preview_id,
        "seed": 0,
        "sample_size_effective": 5,
        "resolved_input": {
            "files": ["versions/v/train.parquet"],
            "column_roles": {"text": "content"},
            "rowkey_scheme": "dw.rowkey/v1",
        },
    }


def _send_and_fail(monkeypatch: pytest.MonkeyPatch, preview_id: str) -> list[tuple[str, dict]]:
    """``send_preview`` for a designer operator, then the designer worker failing the task."""
    sent: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(celery_app, "send_task", lambda name, **kw: sent.append((name, kw)))
    preview.claim(preview_id)
    endpoint.send_preview(_request(preview_id), _dd_entry())
    ((name, options),) = sent
    assert name == "midataworks.designer.preview"
    # The designer worker stores FAILURE in the shared result backend, then sends the errback
    # old-style: the task is not registered there, so Celery prepends the failed task's id.
    task_id = "designer-task-0001"
    celery_app.backend.store_result(task_id, ValueError(DD_MESSAGE), "FAILURE")
    errback = options["link_error"]
    assert errback["task"] == "midataworks.operators.preview_failed"
    assert errback["options"]["queue"] == "preview"
    operator_tasks.preview_failed(task_id, *errback["args"])
    return sent


async def test_a_failed_designer_preview_reads_failed_with_data_designers_message(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    preview_id = _fresh_id()
    _send_and_fail(monkeypatch, preview_id)
    response = await client.get(f"/api/v1/operators/previews/{preview_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "failed"
    assert body["error"]["code"] == "designer_failed"
    assert DD_MESSAGE in body["error"]["message"]
    assert body["error"]["details"]["worker_message"] == DD_MESSAGE
    assert "dataworks_validate_operator_params" in body["error"]["message"]


def test_the_backend_worker_calls_the_errback_new_style(monkeypatch: pytest.MonkeyPatch) -> None:
    """Where the task IS registered (the backend's preview queue), Celery calls the errback with
    ``(request, exc, traceback, *args)``; the failure is taken from the exception itself."""
    preview_id = _fresh_id()
    preview.claim(preview_id)
    operator_tasks.preview_failed(object(), TimeoutError("hard limit"), None, preview_id, "x")
    stored = preview.fetch(preview_id)
    assert stored is not None and stored["status"] == "failed"
    assert stored["error"]["details"] == {
        "exception": "TimeoutError",
        "worker_message": "hard limit",
    }


def test_every_preview_send_carries_its_errback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Native, Data-Juicer and Data Designer previews each link the failure task, pinned."""
    from src.operators.registry import DJ_CATALOGUE

    sent: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(celery_app, "send_task", lambda name, **kw: sent.append((name, kw)))
    reg = OperatorRegistry.build(
        native=(), catalogues=((DJ_CATALOGUE, "datajuicer"),), entry_points=()
    )
    dj = next(e for e, _ in reg.entries())
    native = type("E", (), {"manifest": None})()
    for entry in (_dd_entry(), dj, native):
        endpoint.send_preview({**_request("d" * 64), "operator": "x"}, entry)
    assert [n for n, _ in sent] == [
        "midataworks.designer.preview",
        "midataworks.datajuicer.preview",
        "midataworks.operators.preview",
    ]
    codes = [list(o["link_error"]["args"]) for _, o in sent]
    assert codes == [
        ["d" * 64, "designer_failed"],
        ["d" * 64, "datajuicer_failed"],
        ["d" * 64, "preview_failed"],
    ]
    assert all(o["link_error"]["options"]["queue"] == "preview" for _, o in sent)
    json.dumps([o["link_error"] for _, o in sent])  # serialisable as Celery sends it


async def test_an_unknown_preview_404_no_longer_blames_age(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/operators/previews/" + "0" * 64)
    assert response.status_code == 404
    message = response.json()["error"]["message"]
    assert "finished more than an hour ago" not in message
    assert "FAILED is stored too" in message
