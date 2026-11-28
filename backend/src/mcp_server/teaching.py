"""Tool errors that teach: code first, then the fix, the next calls and the original text (022).

Every tool failure reaches the client as ``CallToolResult(isError=True)`` laid out as (suite
CHAIN_PLAN A13; FR-022.11, .12)::

    <code>: <one-line message>
    fix: <one line>                       (when there is one)
    next: [{"tool", "args", "why"}, ...]  (when there is one)
    <the original text>                   (a backend refusal: "CODE (status): message {details}")

The line-1 code is the error's code lower-cased and valid as the suite contract's ``error_code``
(A30): ``UNPROCESSABLE`` is ``unprocessable`` on line 1 and stays ``UNPROCESSABLE (422): ...`` in
the original text. Argument values never enter the text (``hide_input_in_errors`` on the strict
argument models, and validation errors are read by field location only).

Converting an error means RETURNING a result instead of letting the SDK's handler log and convert
it, so :func:`log_tool_failure` logs first, with the SDK's own three branches (FR-022.32): a crash
with its traceback, an argument refusal by field names, any other failure by its text.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from mcp.shared.exceptions import MCPError
from mcp_types import CallToolResult, TextContent
from pydantic import ValidationError

from .client import DataworksError

#: The logger the SDK's own failure lines use (``mcp/server/mcpserver/server.py``).
logger = logging.getLogger("mcp.server.mcpserver.server")

#: The suite contract's ``error_code`` (mistudio.result-language/v1, ``$defs.error_code``),
#: repeated because the MCP pod reads no vendored file; a test compares the two.
ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)?$")
ERROR_CODE_MAX = 64
#: Line 1's message: up to its first line break, at most this many characters.
LINE_ONE_MAX = 200

#: The server's own transport codes (``client.py``): the fix and the calls that help.
OWN_FIXES: dict[str, tuple[str, list[tuple[str, dict[str, Any], str]]]] = {
    "BACKEND_UNREACHABLE": (
        "Check the backend is up, then retry once.",
        [("dataworks_health", {}, "see what is down")],
    ),
    "BACKEND_TIMEOUT": (
        "The request may have run: read its state before retrying.",
        [
            ("dataworks_list_jobs", {}, "see whether it started"),
            ("dataworks_health", {}, "see what is slow"),
        ],
    ),
    "NON_JSON_ERROR": (
        "The request reached a proxy, not the API.",
        [("dataworks_health", {}, "check the route")],
    ),
    "NON_JSON_RESPONSE": (
        "The answer did not come from the API.",
        [("dataworks_health", {}, "check the route")],
    ),
}


def line_one_code(code: str) -> str:
    """``code`` as line 1 shows it: lower case, ``[^a-z0-9_.]`` as ``_``, else ``error``."""
    lowered = re.sub(r"[^a-z0-9_.]", "_", code.lower())
    return lowered if ERROR_CODE.fullmatch(lowered) and len(lowered) <= ERROR_CODE_MAX else "error"


def log_tool_failure(name: str, exc: ToolError) -> None:
    """Log a tool failure as the SDK's handler would have (SDK ``server.py`` ``_handle_call_tool``),
    because returning a converted result skips that handler."""
    if isinstance(exc, UnexpectedToolError):
        logger.exception("Tool %r raised an unexpected exception", name)
    elif isinstance(exc.__cause__, ValidationError):
        fields = sorted({".".join(str(p) for p in e["loc"]) for e in exc.__cause__.errors()})
        logger.info("Tool %r rejected arguments: %r", name, fields)
    else:
        logger.info("Tool %r failed: %r", name, str(exc))


def _one_line(message: str) -> str:
    first = (message.strip().splitlines() or [""])[0].strip()
    return first[:LINE_ONE_MAX] or "the call failed"


def _next_steps(details: dict[str, Any]) -> list[dict[str, Any]]:
    steps = details.get("next_steps")
    if steps is None:
        legacy = details.get("next")
        steps = [] if legacy is None else (legacy if isinstance(legacy, list) else [legacy])
    return [s for s in steps if isinstance(s, dict)]


def _strip_prefix(name: str, text: str) -> str:
    prefix = f"Error executing tool {name}: "
    return text[len(prefix) :] if text.startswith(prefix) else text


def teach(name: str, exc: ToolError) -> CallToolResult:
    """The code-first ``isError`` result for one tool failure."""
    cause = exc.__cause__
    fix: str | None = None
    steps: list[dict[str, Any]] = []
    if isinstance(exc, UnexpectedToolError):
        code, message, original = "internal_error", "the tool failed on the server", str(exc)
        fix = "This is a server fault, not your call: report it; the server log has the trace."
    elif isinstance(cause, DataworksError):
        details = cause.details or {}
        code, message, original = cause.code, cause.message, str(cause)
        if cause.code in OWN_FIXES:
            fix, own = OWN_FIXES[cause.code]
            steps = [{"tool": t, "args": a, "why": w} for t, a, w in own]
        else:
            steps = _next_steps(details)
            fix = details.get("fix") if isinstance(details.get("fix"), str) else None
            if fix is None and steps:
                fix = str(steps[0].get("why") or "") or None
    elif isinstance(cause, ValidationError):
        text = _strip_prefix(name, str(exc))
        unknown = "unknown argument(s)" in text
        code = "unknown_arguments" if unknown else "invalid_arguments"
        fields = sorted({".".join(str(p) for p in e["loc"]) for e in cause.errors()})
        message = (
            "an argument name this tool does not declare; nothing was run"
            if unknown
            else f"invalid argument(s): {', '.join(fields) or 'the arguments'}; nothing was run"
        )
        accepted = re.search(r"accepts ([^.]*)\. Nothing was run", text)
        fix = (
            f"Use only the arguments it accepts: {accepted.group(1)}."
            if accepted
            else "Correct the named arguments against the tool's input schema."
        )
        original = text
    elif str(exc).startswith("Unknown tool:"):
        code, message, original = "unknown_tool", str(exc), str(exc)
        fix = "Call a tool from tools/list; dataworks_howto lists the workflows."
        steps = [{"tool": "dataworks_howto", "args": {}, "why": "see the tools and workflows"}]
    else:
        original = _strip_prefix(name, str(exc))
        code, message = "tool_error", original
    lines = [f"{line_one_code(code)}: {_one_line(message)}"]
    if fix:
        lines.append(f"fix: {_one_line(fix)}")
    if steps:
        lines.append("next: " + json.dumps(steps, ensure_ascii=False, sort_keys=True))
    lines.append(original)
    return CallToolResult(content=[TextContent(type="text", text="\n".join(lines))], is_error=True)


class DataworksMCPServer(MCPServer):
    """``MCPServer`` whose tool errors teach (022 FR-022.11): every ``ToolError`` the SDK raises,
    an argument refusal included, is logged as the SDK would log it and returned code-first."""

    async def call_tool(self, name: str, arguments: dict[str, Any], context: Any = None) -> Any:
        try:
            return await super().call_tool(name, arguments, context)
        except MCPError:
            raise
        except ToolError as exc:  # UnexpectedToolError included
            log_tool_failure(name, exc)
            return teach(name, exc)
