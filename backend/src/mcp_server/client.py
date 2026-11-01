# Origin: miStudio (Onegaishimas/miStudio) backend/src/mcp_server/client.py @ c829a2cc
# Mode: adapt (docs/REUSE.md). Kept: one async client as the only path from a tool to the
# backend; `get(path, **params)` / `post(path, json_body, **params)` signatures. Changed: the agent
# header on every request; the error envelope unwrapped; a 2xx non-JSON body is an ERROR (the guard
# miStudio's millm_client.py has and its client.py lacks); `put`/`patch` require a body; a
# multipart upload method (010 FTID section 3.2).
"""The only path from an MCP tool to the miDataworks backend (FR-010.4, FR-010.7, FR-010.8).

Guarantees:

- **Every request carries ``X-Dataworks-Agent``.** It is set on the client's default headers AND
  re-asserted inside :meth:`DataworksClient.request`, which takes no ``headers`` argument, so no
  tool can send a request the REST layer would take for the operator's. The approval gate
  (ADR-013) keys on this header; a request without it runs ungated.
- **A 2xx body that is not JSON is an error,** ``NON_JSON_RESPONSE``. A misrouted ingress answers
  200 with an HTML page; miStudio's client returned that to the agent as an empty success.
- **An error envelope surfaces its code, message and details** as a :class:`DataworksError`.
- **A read timeout says the request may already have been applied,** because a POST that timed
  out on the read may have run.

A ``202`` pending-approval body is returned unchanged: it is the tool's result.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

logger = logging.getLogger(__name__)

#: Must equal ``AGENT_HEADER`` in ``backend/src/core/agent_origin.py``; compared by AST in
#: ``tests/unit/mcp/test_client.py`` (this package must not import the backend).
AGENT_HEADER = "X-Dataworks-Agent"


class DataworksError(ToolError):
    """The backend refused, failed, or could not be reached. Carries the envelope verbatim.

    A ``ToolError`` so the agent reads the envelope: mcp 2.x shows a tool's message to the
    client only for a ``ToolError``, and answers any other exception with the bare text
    ``Error executing tool <name>`` (1.x showed every exception's text). As a plain
    ``Exception`` every refusal, 409 and validation message would reach the agent as that
    one line.
    """

    def __init__(
        self, status: int, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}
        suffix = f" {json.dumps(self.details, sort_keys=True)}" if self.details else ""
        super().__init__(f"{code} ({status}): {message}{suffix}")


def _clean(params: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in params.items() if v is not None}


class DataworksClient:
    """Thin async wrapper over ``{base_url}/api/v1`` (and ``/api/health``)."""

    def __init__(self, base_url: str, identity: str, timeout: float = 60.0) -> None:
        self._identity = identity
        self._root = base_url.rstrip("/")
        self._http = httpx.AsyncClient(
            base_url=f"{self._root}/api/v1",
            timeout=timeout,
            headers={AGENT_HEADER: identity},
        )

    @property
    def identity(self) -> str:
        return self._identity

    async def close(self) -> None:
        await self._http.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        files: list[tuple[str, tuple[str, bytes, str]]] | None = None,
        data: dict[str, str] | None = None,
    ) -> Any:
        """Send one request. ``path`` is relative to ``/api/v1``; an absolute ``/api/...`` path
        outside it (``/api/health``) is sent to the backend root."""
        url = f"{self._root}{path}" if path.startswith("/api/") else path
        # Re-asserted per call: the default header could be dropped by a future refactor of the
        # constructor; this line cannot be bypassed by any argument, because there is none.
        headers = {AGENT_HEADER: self._identity}
        try:
            response = await self._http.request(
                method,
                url,
                params=params or None,
                json=json_body,
                files=files,
                data=data,
                headers=headers,
            )
        except httpx.ReadTimeout as exc:
            raise DataworksError(
                0,
                "BACKEND_TIMEOUT",
                f"The backend did not answer {method} {path} in time. The request may already "
                "have been applied: check its state before retrying.",
            ) from exc
        except httpx.HTTPError as exc:
            raise DataworksError(
                0,
                "BACKEND_UNREACHABLE",
                f"The miDataworks backend could not be reached ({type(exc).__name__}).",
            ) from exc

        content_type = response.headers.get("content-type", "")
        is_json = "json" in content_type
        if response.status_code >= 400:
            if is_json:
                try:
                    body = response.json()
                except ValueError:
                    body = None
                error = body.get("error") if isinstance(body, dict) else None
                if isinstance(error, dict):
                    raise DataworksError(
                        response.status_code,
                        str(error.get("code") or "ERROR"),
                        str(error.get("message") or ""),
                        error.get("details") if isinstance(error.get("details"), dict) else None,
                    )
            raise DataworksError(
                response.status_code,
                "NON_JSON_ERROR",
                f"The backend answered {response.status_code} without an error envelope; the "
                "request may have reached a proxy rather than the API.",
            )

        if not response.content:
            return {}
        if not is_json:
            raise DataworksError(
                response.status_code,
                "NON_JSON_RESPONSE",
                f"{method} {path} answered {response.status_code} with {content_type or 'no'} "
                "content, which did not come from the API (misrouted ingress or proxy page). "
                "This is not an empty result.",
            )
        try:
            return response.json()
        except ValueError as exc:
            raise DataworksError(
                response.status_code,
                "NON_JSON_RESPONSE",
                f"{method} {path} answered with a body that is not valid JSON.",
            ) from exc

    async def get(self, path: str, **params: Any) -> Any:
        return await self.request("GET", path, params=_clean(params))

    async def post(self, path: str, json_body: Any = None, **params: Any) -> Any:
        return await self.request("POST", path, params=_clean(params), json_body=json_body)

    async def put(self, path: str, json_body: Any) -> Any:
        return await self.request("PUT", path, json_body=json_body)

    async def patch(self, path: str, json_body: Any) -> Any:
        return await self.request("PATCH", path, json_body=json_body)

    async def delete(self, path: str, json_body: Any = None, **params: Any) -> Any:
        return await self.request("DELETE", path, params=_clean(params), json_body=json_body)

    async def post_multipart(
        self,
        path: str,
        *,
        files: list[tuple[str, tuple[str, bytes, str]]],
        data: dict[str, str],
    ) -> Any:
        return await self.request("POST", path, files=files, data=data)
