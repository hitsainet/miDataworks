"""The error envelope and its handlers (Foundation task 3.2)."""

from __future__ import annotations

import logging

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.core.errors import AppError, ConflictError, NotFoundError, install_error_handlers

SECRET_DETAIL = "db password is hunter2 at line 42"


class Body(BaseModel):
    n: int


def _app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    @app.post("/validate")
    async def validate(body: Body) -> dict[str, int]:
        return {"n": body.n}

    @app.get("/missing")
    async def missing() -> None:
        raise NotFoundError("No such thing.", code="THING_NOT_FOUND")

    @app.get("/conflict")
    async def conflict() -> None:
        raise ConflictError("Already done.", details={"status": "completed"})

    @app.get("/http")
    async def http() -> None:
        raise HTTPException(status_code=404, detail="gone")

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError(SECRET_DETAIL)

    @app.get("/refuse")
    async def refuse() -> None:
        raise AppError("Set your name first.", code="NO_IDENTITY", status_code=422)

    return app


@pytest.fixture
async def http() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=_app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        yield client


async def test_validation_errors_use_the_envelope(http: httpx.AsyncClient) -> None:
    response = await http.post("/validate", json={"n": "not a number"})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"]["errors"][0]["loc"] == ["body", "n"]


async def test_not_found_uses_the_envelope(http: httpx.AsyncClient) -> None:
    response = await http.get("/missing")
    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "THING_NOT_FOUND", "message": "No such thing.", "details": {}}
    }


async def test_conflict_uses_the_envelope(http: httpx.AsyncClient) -> None:
    response = await http.get("/conflict")
    assert response.status_code == 409
    assert response.json()["error"]["details"] == {"status": "completed"}


async def test_framework_http_errors_use_the_envelope(http: httpx.AsyncClient) -> None:
    response = await http.get("/http")
    assert response.json()["error"] == {"code": "NOT_FOUND", "message": "gone", "details": {}}
    unrouted = await http.get("/no-such-route")
    assert unrouted.status_code == 404
    assert unrouted.json()["error"]["code"] == "NOT_FOUND"


async def test_app_error_carries_its_code_and_status(http: httpx.AsyncClient) -> None:
    response = await http.get("/refuse")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "NO_IDENTITY"


async def test_unexpected_errors_log_the_trace_and_return_none(
    http: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR):
        response = await http.get("/boom")
    assert response.status_code == 500
    body = response.text
    assert SECRET_DETAIL not in body and "Traceback" not in body and "RuntimeError" not in body
    error = response.json()["error"]
    assert error["code"] == "INTERNAL_ERROR"
    correlation = error["details"]["correlation_id"]
    logged = [r for r in caplog.records if correlation in r.getMessage()]
    assert logged and logged[0].exc_info is not None, "the stack trace must reach the log"
