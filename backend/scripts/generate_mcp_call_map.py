"""Generate ``docs/mcp-annotations.json``: every MCP tool's title, hints and REST calls (022 FR-022.26).

miWorkbench's ``dataworks_*`` relays carry exactly the source tool's hints only when they make the
same calls (suite CHAIN_PLAN A13 as amended by A30), so the published table names, per tool,
**every** REST call it can make, on every branch: the method and the route's OpenAPI path template
as the live app publishes it (``/api/v1/...``).

One replay sees one branch only (``dataworks_get_version_manifest`` posts a publish build only when
no files exist), so the calls are read from each tool's code: an AST pass over the tool function
(its lambdas included, and any module-level helper of ``mcp_server/`` it calls by name) collects
every ``client.<method>(<path>)``; each path, a literal or an f-string, is matched against the live
OpenAPI ``paths`` with its method. No match, or more than one equally specific match, aborts naming
the tool. ``tests/unit/mcp/test_annotation_calls_pinned.py`` replays every tool itself and pins this
map, so a generator bug cannot agree with itself.

Titles and hints come from the LIVE registry (``build_server`` then ``list_tools()``), never from
the table's module.

This lives in ``backend/scripts`` and not in ``mcp_server/contract.py`` because it reads the
backend app's OpenAPI document, and nothing under ``mcp_server/`` may import the backend
(``tests/unit/test_mcp_tools_stay_on_http.py``, ADR-016).

Regenerate (the test environment provides the configuration)::

    cd backend && python -m scripts.generate_mcp_call_map --write
    cd backend && python -m scripts.generate_mcp_call_map --check
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

ANNOTATIONS_PATH = Path(__file__).resolve().parents[2] / "docs" / "mcp-annotations.json"

#: The client's methods and the HTTP method each sends (``mcp_server/client.py``).
VERBS = {
    "get": "GET",
    "post": "POST",
    "put": "PUT",
    "patch": "PATCH",
    "delete": "DELETE",
    "post_multipart": "POST",
}
API = "/api/v1"
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


class CallMapError(Exception):
    """A tool call the generator cannot place on exactly one live route."""


def _is_tool(node: ast.AST) -> bool:
    if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
        return False
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Attribute) and target.attr == "tool":
            return True
    return False


def _path_of(arg: ast.AST, tool: str) -> str:
    """The raw path of one client call: a literal, or an f-string with ``{}`` for each value."""
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    if isinstance(arg, ast.JoinedStr):
        parts: list[str] = []
        for value in arg.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            elif isinstance(value, ast.FormattedValue):
                parts.append("{}")
        return "".join(parts)
    raise CallMapError(f"{tool}: a client call whose path is not a literal or an f-string")


def _client_calls(node: ast.AST, tool: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for sub in ast.walk(node):
        if not (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)):
            continue
        if sub.func.attr not in VERBS:
            continue
        if not (isinstance(sub.func.value, ast.Name) and sub.func.value.id == "client"):
            continue
        if not sub.args:
            raise CallMapError(f"{tool}: client.{sub.func.attr}() without a path")
        found.append((VERBS[sub.func.attr], _path_of(sub.args[0], tool)))
    return found


def _called_names(node: ast.AST) -> set[str]:
    return {
        sub.func.id
        for sub in ast.walk(node)
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
    }


def raw_calls(modules: list[ModuleType]) -> dict[str, list[tuple[str, str]]]:
    """``{tool: [(METHOD, raw path)]}`` for every tool the modules declare, every branch.

    A helper defined at module level in the same module and called by name is walked too (and the
    helpers it calls), so a call hidden in a helper is not missed.
    """
    out: dict[str, list[tuple[str, str]]] = {}
    for module in modules:
        tree = ast.parse(inspect.getsource(module))
        helpers = {
            n.name: n
            for n in tree.body
            if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef) and n.name != "register"
        }
        for node in ast.walk(tree):
            if not _is_tool(node):
                continue
            name = node.name  # type: ignore[attr-defined]
            calls = _client_calls(node, name)
            seen: set[str] = set()
            todo = _called_names(node) & set(helpers)
            while todo:
                helper = todo.pop()
                if helper in seen:
                    continue
                seen.add(helper)
                calls += _client_calls(helpers[helper], name)
                todo |= _called_names(helpers[helper]) & set(helpers)
            out[name] = sorted(set(calls))
    return out


def _segments(path: str) -> list[str]:
    return path.strip("/").split("/")


def _is_param(segment: str) -> bool:
    return segment.startswith("{") and segment.endswith("}")


def template_for(method: str, raw: str, paths: dict[str, Any], tool: str) -> str:
    """The one live OpenAPI template ``raw`` reaches with ``method``.

    ``raw`` is relative to ``/api/v1`` unless it starts ``/api/`` (``client.py``). A ``{}`` in it
    matches only a template parameter; a literal segment matches the same literal or a parameter.
    Of several matches the one with the fewest parameters wins (FastAPI's literal routes are
    declared before their parameterised siblings); a tie aborts.
    """
    full = raw if raw.startswith("/api/") else API + raw
    if "?" in full:
        raise CallMapError(f"{tool}: {method} {raw} carries a query string in its path")
    want = _segments(full)
    matches: list[tuple[int, str]] = []
    for template, operations in paths.items():
        if method.lower() not in operations:
            continue
        have = _segments(template)
        if len(have) != len(want):
            continue
        ok = True
        params = 0
        for w, h in zip(want, have, strict=True):
            if _is_param(h):
                params += 1
                continue
            if w != h:
                ok = False
                break
        if ok:
            matches.append((params, template))
    if not matches:
        raise CallMapError(f"{tool}: {method} {full} matches no live route")
    matches.sort()
    if len(matches) > 1 and matches[0][0] == matches[1][0]:
        raise CallMapError(f"{tool}: {method} {full} matches {[t for _, t in matches]}")
    return matches[0][1]


def call_map(openapi_paths: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    """``{tool: [{"method", "path"}]}`` with full OpenAPI templates, sorted by (path, method)."""
    from src.mcp_server.tools import CATEGORY_MODULES

    modules = [m for mods in CATEGORY_MODULES.values() for m in mods]
    out: dict[str, list[dict[str, str]]] = {}
    for tool, calls in raw_calls(modules).items():
        templated = {
            (template_for(method, raw, openapi_paths, tool), method) for method, raw in calls
        }
        out[tool] = [{"method": m, "path": p} for p, m in sorted(templated)]
    return out


def live_openapi_paths() -> dict[str, Any]:
    from src.main import fastapi_app

    paths: dict[str, Any] = fastapi_app.openapi()["paths"]
    return paths


def live_tools() -> list[Any]:
    from src.mcp_server.config import MCPSettings
    from src.mcp_server.server import build_server

    mcp, _ = build_server(MCPSettings(auth_token="annotation-generation"))  # noqa: S106
    return asyncio.run(mcp.list_tools())


def annotations_document() -> str:
    """The published table, byte-stable: keys sorted, two-space indent, trailing newline."""
    calls = call_map(live_openapi_paths())
    tools: dict[str, Any] = {}
    for tool in live_tools():
        hints = tool.annotations
        tools[tool.name] = {
            "title": tool.title,
            "readOnlyHint": hints.read_only_hint,
            "destructiveHint": hints.destructive_hint,
            "calls": calls[tool.name],
        }
    document = {"server": "midataworks", "version": 2, "tools": tools}
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_annotations(path: Path = ANNOTATIONS_PATH) -> None:
    path.write_text(annotations_document(), encoding="utf-8")


def _test_environment() -> None:
    """Placeholders the backend's settings need to BUILD the app (never served); the same the
    test suite sets in ``tests/conftest.py``."""
    import os

    for key, value in {
        "DATABASE_URL": "postgresql+asyncpg://midataworks:unused@127.0.0.1:1/midataworks_test",
        "REDIS_URL": "redis://127.0.0.1:1/15",
        "SETTINGS_ENCRYPTION_KEY": "test-only-encryption-key-0123456789abcdef",
        "INTERNAL_API_SECRET": "test-only-internal-secret-0123456789",
    }.items():
        os.environ.setdefault(key, value)


def main(argv: list[str]) -> int:
    _test_environment()
    if argv[1:] == ["--write"]:
        write_annotations()
        return 0
    if argv[1:] == ["--check"]:
        current = ANNOTATIONS_PATH.read_text(encoding="utf-8") if ANNOTATIONS_PATH.exists() else ""
        if current != annotations_document():
            print(f"{ANNOTATIONS_PATH} is stale: run with --write", file=sys.stderr)
            return 1
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))


__all__ = [
    "ANNOTATIONS_PATH",
    "CallMapError",
    "annotations_document",
    "call_map",
    "raw_calls",
    "template_for",
    "write_annotations",
]
