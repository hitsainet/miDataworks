"""The single error envelope and its handlers (ADR-013; Foundation task 3.2).

Every error leaves the API as ``{"error": {"code", "message", "details"}}``. Success bodies are
plain JSON resources.

An unexpected exception is logged WITH its stack trace and answered WITHOUT one: the response
carries a fixed message and a correlation id that finds the log line. Returning ``str(exc)`` or a
traceback to the caller is the stack-trace-exposure defect miStudio fixed across six endpoints.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    """A refusal with a reason. Raise it from services and routes; the handler renders it."""

    status_code: int = 400
    code: str = "BAD_REQUEST"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.details = details or {}


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"


class ForbiddenError(AppError):
    status_code = 403
    code = "FORBIDDEN"


class UnprocessableError(AppError):
    status_code = 422
    code = "UNPROCESSABLE"


def envelope(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    """The one error body shape."""
    return {"error": {"code": code, "message": message, "details": details or {}}}


_HTTP_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    422: "VALIDATION_ERROR",
    429: "TOO_MANY_REQUESTS",
    503: "UNAVAILABLE",
}


async def _app_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return JSONResponse(
        status_code=exc.status_code, content=envelope(exc.code, exc.message, exc.details)
    )


async def _http_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
    message = exc.detail if isinstance(exc.detail, str) else code.replace("_", " ").lower()
    return JSONResponse(
        status_code=exc.status_code,
        content=envelope(code, message),
        headers=getattr(exc, "headers", None),
    )


#: Pydantic custom error types that are domain codes in their own right.
DOMAIN_VALIDATION_CODES = frozenset({"repo_id_invalid"})


async def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    problems = [
        {
            "loc": [str(p) for p in err.get("loc", ())],
            "msg": err.get("msg", ""),
            "type": err.get("type", ""),
        }
        for err in exc.errors()
    ]
    # A field rule that names its own domain code (``repo_id_invalid``, 001 FTDD 5.2) is reported
    # under that code with its own next-step message, so a client can act on it.
    types = {p["type"] for p in problems}
    if len(types) == 1 and (only := types.pop()) in DOMAIN_VALIDATION_CODES:
        return JSONResponse(
            status_code=422, content=envelope(only, problems[0]["msg"], {"errors": problems})
        )
    return JSONResponse(
        status_code=422,
        content=envelope("VALIDATION_ERROR", "The request is not valid.", {"errors": problems}),
    )


async def _unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    correlation_id = uuid.uuid4().hex[:12]
    logger.exception(
        "Unhandled error %s on %s %s", correlation_id, request.method, request.url.path
    )
    return JSONResponse(
        status_code=500,
        content=envelope(
            "INTERNAL_ERROR",
            "Something went wrong on the server. The log line carries this correlation id.",
            {"correlation_id": correlation_id},
        ),
    )


def install_error_handlers(app: FastAPI) -> None:
    """Register every handler. Called once by ``main.create_app``."""
    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(HTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _unexpected_error)
