# Origin: miStudio (Onegaishimas/miStudio) backend/src/mcp_server/server.py @ c829a2cc
# Mode: adapt (docs/REUSE.md). Kept: BearerAuthMiddleware (UTF-8 constant-time compare, /health
# exempt), both startup refusals, the stateless JSON server, the non-blocking /health, the audit
# wrapper, registry-derived instruction counts. Changed: the audit line carries the agent identity
# and secret arguments are redacted before the digest; one registry and one register signature;
# /health reports per-dependency state (010 FTID section 3.5); MCP SDK 2.x (``MCPServer``, the
# transport settings on the app factory, see ``build_http_app``).
"""miDataworks MCP server assembly: MCPServer (mcp SDK 2.x), bearer auth, category gating, audit (FR-010.1-.14).

Auth (FR-010.3): the streamable-HTTP transport requires ``Authorization: Bearer
<MCP_AUTH_TOKEN>``. Start is refused without a token, and ``MCP_ALLOW_ANONYMOUS`` is honoured on
stdio only: miStudio once let the flag alone satisfy the guard on HTTP, which served destructive
tools unauthenticated on the LAN.
"""

from __future__ import annotations

import ast
import hashlib
import hmac
import inspect
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.mcpserver import MCPServer
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .client import DataworksClient
from .config import MCPSettings
from .context import SECRET_PARAMS, ToolContext
from .health_gate import DEPENDENCIES, HealthGate
from .tools import CATEGORY_MODULES
from .tools.datasets import UPLOAD_MAX_BYTES

logger = logging.getLogger(__name__)

SERVER_INSTRUCTIONS = """\
miDataworks MCP server: import, curate, label, check and publish datasets.

**Call `dataworks_howto` first.** {tool_count} tools across {category_count} categories; that tool
holds the workflows and the three result shapes.

Every tool returns one of three things:
1. the backend's resource;
2. a pending approval, {"approval_id": ..., "status": "pending", ...}: the action waits for the
   operator in miDataworks. Poll `dataworks_get_approval_status`. You cannot approve it yourself;
3. {"unavailable": "<dependency>", "reason": ...}: the backend or one of its dependencies is down.
   Report the reason; do not retry in a loop.

Long work runs as a job: poll `dataworks_job_status`.
"""

REDACTED = "<redacted>"

#: The largest MCP request body the HTTP app accepts. mcp 2.x refuses a body over 4 MiB with a 413
#: by default, which is below the 10 MiB file ``dataworks_upload_file`` accepts once base64 makes it
#: 4/3 larger (about 13.3 MiB). Derived from the upload cap, plus 1 MiB for the JSON-RPC envelope
#: and the other arguments, so the two cannot drift apart. mcp 1.x had no limit at all.
MAX_REQUEST_BODY_BYTES = (UPLOAD_MAX_BYTES * 4) // 3 + 1024 * 1024


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Constant-time bearer-token check on every HTTP request except ``/health``."""

    def __init__(self, app: Any, token: str) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path == "/health":
            return await call_next(request)
        header = request.headers.get("authorization", "")
        provided = header[7:] if header.lower().startswith("bearer ") else ""
        # Bytes, not str: `hmac.compare_digest` raises TypeError on a non-ASCII str, which turned a
        # wrong token into a 500 on an unauthenticated port (miStudio MIS-E2E-117).
        if not provided or not hmac.compare_digest(
            provided.encode("utf-8"), self._token.encode("utf-8")
        ):
            return JSONResponse({"detail": "Invalid or missing bearer token"}, status_code=401)
        return await call_next(request)


def redact(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """The arguments with every ``SECRET_PARAMS`` value replaced by ``"<redacted>"``."""
    secret = SECRET_PARAMS.get(tool, frozenset())
    return {k: (REDACTED if k in secret and v is not None else v) for k, v in args.items()}


class AuditToolLogger:
    """One structured line per tool call, carrying the caller identity (FR-010.5)."""

    @staticmethod
    def digest(tool: str, args: dict[str, Any]) -> str:
        canonical = json.dumps(redact(tool, args), sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()[:12]

    @staticmethod
    def log(
        tool: str, identity: str, args: dict[str, Any], status: str, duration_ms: float
    ) -> None:
        logger.info(
            "mcp_tool_call tool=%s identity=%s args_digest=%s status=%s duration_ms=%.0f",
            tool,
            identity,
            AuditToolLogger.digest(tool, args),
            status,
            duration_ms,
        )


def tool_names_declared(module: Any) -> list[str]:
    """Tool names a module declares, read from its AST (``@mcp.tool()`` decorators).

    Not obtained by registering the module, which would ask the registration path to vouch for
    itself; the reachability test compares this with the live registry.
    """
    tree = ast.parse(inspect.getsource(module))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                if isinstance(target, ast.Attribute) and target.attr == "tool":
                    names.append(node.name)
                    break
    return names


def _server_instructions() -> str:
    """The instructions with counts DERIVED from the tool modules (FR-010.21).

    miStudio's hand-written counts drifted three ways at once. Counted by AST because building a
    server here would recurse (``build_server`` calls this).
    """
    tool_count = sum(
        len(tool_names_declared(module))
        for modules in CATEGORY_MODULES.values()
        for module in modules
    )
    # `.replace`, not `.format`: the text carries literal braces.
    return SERVER_INSTRUCTIONS.replace("{tool_count}", str(tool_count)).replace(
        "{category_count}", str(len(CATEGORY_MODULES))
    )


def build_server(settings: MCPSettings, stdio: bool = False) -> tuple[MCPServer, DataworksClient]:
    """Create the MCPServer with only the enabled categories registered (FR-010.2, .9)."""
    if not settings.auth_token and not stdio:
        raise SystemExit(
            "MCP_AUTH_TOKEN is required: the MCP port is reachable on the LAN. Set a token, or use "
            "the stdio transport with MCP_ALLOW_ANONYMOUS=true for local development only."
        )
    if settings.allow_anonymous and not stdio:
        raise SystemExit(
            "MCP_ALLOW_ANONYMOUS is honoured on the stdio transport only. Over HTTP the port is "
            "reachable on the LAN and exposes publishing and deletion, so anonymous access is "
            "refused. Set MCP_AUTH_TOKEN instead."
        )
    if stdio and not settings.auth_token and not settings.allow_anonymous:
        raise SystemExit(
            "Set MCP_ALLOW_ANONYMOUS=true to run the stdio transport without MCP_AUTH_TOKEN."
        )

    categories = settings.enabled_categories()
    # mcp 2.x: name positional, everything else by keyword (its second positional slot is now
    # `title`, which would silently swallow the instructions). The transport settings live on
    # `build_http_app`, not here.
    mcp = MCPServer("midataworks", instructions=_server_instructions())
    client = DataworksClient(settings.api_url, settings.agent_identity)
    gate = HealthGate(settings.api_url)
    ctx = ToolContext(settings=settings, gate=gate)

    registered: list[str] = []
    for category, modules in CATEGORY_MODULES.items():
        if category not in categories:
            continue
        for module in modules:
            module.register(mcp, client, ctx)
        registered.append(category)
    logger.info("MCP tool categories enabled: %s", sorted(registered))

    @mcp.custom_route("/health", methods=["GET"])  # type: ignore[untyped-decorator]
    async def health(request: Request) -> JSONResponse:
        """Never blocks on a probe (snapshots only); reasons are coarse because the route is
        unauthenticated and detailed reasons carry internal URLs."""
        dependencies = {}
        for dep in DEPENDENCIES:
            available, reason = gate.snapshot(dep)
            dependencies[dep] = {"available": available, "reason": gate.public_reason(reason)}
        body: dict[str, Any] = {
            "status": "ok",
            "service": "midataworks-mcp",
            "categories": sorted(registered),
            "dependencies": dependencies,
        }
        unknown = sorted(settings.unknown_categories())
        if unknown:
            body["unknown_categories"] = unknown
        return JSONResponse(body)

    async def _close_backend_clients() -> None:
        for close in (client.close, gate.aclose):
            try:
                await close()
            except Exception:  # noqa: BLE001 - best-effort shutdown
                logger.debug("Closing an MCP backend client failed", exc_info=True)

    mcp.close_backend_clients = _close_backend_clients  # type: ignore[attr-defined]
    mcp.dataworks_identity = settings.agent_identity  # type: ignore[attr-defined]
    return mcp, client


def build_http_app(mcp: MCPServer, settings: MCPSettings) -> Starlette:
    """The streamable-HTTP ASGI app ``__main__`` serves: stateless JSON, bearer-gated.

    mcp 1.x took these on the server's constructor; 2.x takes them here. ``host`` is passed on
    purpose: the app factory defaults to ``127.0.0.1``, and that default switches on DNS-rebinding
    protection with an allow-list of loopback ``Host`` headers only, so every request arriving as
    ``midataworks-mcp:8765`` (the in-cluster service) or through the ingress would be answered
    ``421 Invalid Host header``. With the bind host (``0.0.0.0`` in the pod) the protection stays
    off, as it was under 1.x; the bearer token is the gate.
    """
    app = mcp.streamable_http_app(
        json_response=True,
        stateless_http=True,
        host=settings.host,
        max_request_body_size=MAX_REQUEST_BODY_BYTES,
    )
    app.add_middleware(BearerAuthMiddleware, token=settings.auth_token)
    return app


def wrap_tool_with_audit(mcp: MCPServer) -> int:
    """Wrap every registered tool's function with the audit logger. Returns the count wrapped."""
    identity = getattr(mcp, "dataworks_identity", "agent:unknown")
    manager = mcp._tool_manager  # noqa: SLF001 - the SDK offers no public hook
    wrapped = 0
    for tool in manager.list_tools():
        original = tool.fn

        def make_wrapper(
            fn: Callable[..., Awaitable[Any]], tool_name: str
        ) -> Callable[..., Awaitable[Any]]:
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                start = time.monotonic()
                try:
                    result = await fn(*args, **kwargs)
                except Exception as exc:
                    AuditToolLogger.log(
                        tool_name,
                        identity,
                        kwargs,
                        f"error:{type(exc).__name__}",
                        (time.monotonic() - start) * 1000,
                    )
                    raise
                status = (
                    "pending"
                    if isinstance(result, dict)
                    and result.get("status") == "pending"
                    and "approval_id" in result
                    else (
                        "unavailable"
                        if isinstance(result, dict) and "unavailable" in result
                        else "ok"
                    )
                )
                AuditToolLogger.log(
                    tool_name, identity, kwargs, status, (time.monotonic() - start) * 1000
                )
                return result

            return wrapper

        tool.fn = make_wrapper(original, tool.name)
        wrapped += 1
    return wrapped
