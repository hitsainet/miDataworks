"""Generate ``backend/src/mcp_server/output_schemas.json``: an ``outputSchema`` per typed tool (022 FR-022.21).

For each tool in the LIVE registry, the route its documented call reaches (the route ledger,
``tests/support/mcp_ledger.py``, templated against the live OpenAPI document) gives the 2xx
``application/json`` body. A body that is a typed object (a component with properties) or a typed
list (an array of such components) gets a schema; every other tool is listed in ``untyped`` with its
route and the reason. Typing the untyped routes is debt (BRD-MIDATAWORKS-MCPUX-001 section 6).

Each schema is PERMISSIVE and honest (FR-022.22):

- the root is ``{"type": "object", "properties": {...}}`` with no ``required`` list, and no
  ``required`` or ``additionalProperties: false`` anywhere below it either, so a body the tool trims
  or extends still conforms; only a type the route promises is checked;
- the contract's optional ``approval``, ``next_steps``, ``unavailable`` and ``reason`` and the legacy
  pending keys are declared beside the route's own (a route property of the same name accepts
  either), so a pending approval and an ``unavailable`` answer conform;
- the legacy ``next`` is NEVER constrained (A30, R1A-14): a route property ``next`` is removed;
- a list route's schema is one property, ``result``, the array (the tool answers
  ``structuredContent {"result": [...]}``); an object route's own ``result`` property is left
  unconstrained, because a non-dict answer is structured under that key.

The MCP pod reads the JSON file and imports nothing from the backend (ADR-016).

Regenerate, or check (exits 1 on a difference)::

    cd backend && python -m scripts.generate_output_schemas --write
    cd backend && python -m scripts.generate_output_schemas --check
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

SCHEMAS_PATH = Path(__file__).resolve().parents[1] / "src" / "mcp_server" / "output_schemas.json"
REF = "#/components/schemas/"

#: The contract's ``next_step``, inlined (the pod reads no vendored file); permissive.
NEXT_STEP = {
    "type": "object",
    "properties": {
        "tool": {"type": "string"},
        "args": {"type": "object"},
        "why": {"type": "string"},
    },
}
#: Keys any tool's answer may carry beside the route's own: the contract's and the legacy pending
#: answer's (``core/agent_origin.py``), all optional.
SHARED: dict[str, dict[str, Any]] = {
    "approval": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "status": {"type": "string"},
            "decide_in": {"type": "string"},
            "then": {"type": "string"},
            "summary": {"type": "string"},
        },
    },
    "next_steps": {"type": "array", "items": NEXT_STEP},
    "unavailable": {"type": "string"},
    "reason": {"type": ["string", "null"]},
    "approval_id": {"type": "string"},
    "status": {"type": "string"},
    "action": {"type": "string"},
    "request_digest": {"type": "string"},
    "expires_at": {"type": "string"},
    "hint": {"type": "string"},
}


def _relax(node: Any) -> Any:
    """Drop ``required`` and ``additionalProperties: false`` everywhere; rewrite refs to ``$defs``."""
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key == "required":
                continue
            if key == "additionalProperties" and value is False:
                continue
            if key == "$ref" and isinstance(value, str) and value.startswith(REF):
                out[key] = "#/$defs/" + value[len(REF) :]
                continue
            out[key] = _relax(value)
        return out
    if isinstance(node, list):
        return [_relax(v) for v in node]
    return node


def _refs(node: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and value.startswith(REF):
                found.add(value[len(REF) :])
            else:
                found |= _refs(value)
    elif isinstance(node, list):
        for value in node:
            found |= _refs(value)
    return found


def _reachable(start: set[str], components: dict[str, Any]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    todo = set(start)
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen[name] = components[name]
        todo |= _refs(components[name]) - set(seen)
    return dict(sorted(seen.items()))


def _body_schemas(operation: dict[str, Any]) -> list[dict[str, Any]]:
    """Every distinct 2xx ``application/json`` body the route declares."""
    found: list[dict[str, Any]] = []
    for code in sorted(operation.get("responses", {})):
        if not code.startswith("2"):
            continue
        schema = (
            operation["responses"][code]
            .get("content", {})
            .get("application/json", {})
            .get("schema")
        )
        if schema and schema not in found:
            found.append(dict(schema))
    return found


def _is_object_ref(schema: dict[str, Any], components: dict[str, Any]) -> bool:
    ref = schema.get("$ref", "")
    return ref.startswith(REF) and bool(components[ref[len(REF) :]].get("properties"))


def schema_for(
    operation: dict[str, Any], components: dict[str, Any]
) -> tuple[dict[str, Any] | None, str]:
    """The tool's schema, or None and the reason it is untyped."""
    bodies = _body_schemas(operation)
    if not bodies:
        return None, "the route declares no JSON body"
    if len(bodies) > 1:
        # Review R2 finding 4: a 200 resource and a 202 acceptance are two answers; publishing the
        # first would describe the wrong one.
        return None, "the route answers one of several bodies (by status)"
    body = bodies[0]
    if _is_object_ref(body, components):
        root = components[body["$ref"][len(REF) :]]
        properties = copy.deepcopy(root["properties"])
        refs = _refs(properties)
    elif body.get("type") == "array" and _is_object_ref(body.get("items", {}), components):
        properties = {"result": copy.deepcopy(body)}
        refs = _refs(body)
    elif "anyOf" in body:
        return None, "the route answers one of several bodies (anyOf)"
    else:
        return None, "the route's body is an untyped object (dict, Any or a Response)"
    properties.pop("next", None)  # A30: the legacy next is never constrained
    if "result" in properties and not (body.get("type") == "array"):
        # A non-dict answer of this tool is structured as {"result": value} (A30), so a route's
        # own ``result`` property cannot constrain that key.
        properties["result"] = {}
    for key, shared in SHARED.items():
        properties[key] = {"anyOf": [properties[key], shared]} if key in properties else shared
    schema: dict[str, Any] = {"type": "object", "properties": dict(sorted(properties.items()))}
    defs = _reachable(refs, components)
    if defs:
        schema["$defs"] = defs
    return _relax(schema), ""


def generate() -> dict[str, Any]:
    import asyncio

    from scripts.generate_mcp_call_map import template_for
    from src.main import fastapi_app
    from src.mcp_server.config import MCPSettings
    from src.mcp_server.server import build_server
    from tests.support.mcp_ledger import CALLER_ASSERTION_EXEMPT, EXPECTED_CALLS

    document = fastapi_app.openapi()
    paths, components = document["paths"], document["components"]["schemas"]
    mcp, _ = build_server(MCPSettings(auth_token="schema-generation"))  # noqa: S106
    tools: dict[str, Any] = {}
    untyped: dict[str, Any] = {}
    for tool in sorted(t.name for t in asyncio.run(mcp.list_tools())):
        if tool in CALLER_ASSERTION_EXEMPT:
            untyped[tool] = {"route": None, "reason": "served by the MCP server; calls nothing"}
            continue
        method, path, _, _ = EXPECTED_CALLS[tool]
        template = template_for(method, path, paths, tool)
        schema, reason = schema_for(paths[template][method.lower()], components)
        if schema is None:
            untyped[tool] = {"route": f"{method} {template}", "reason": reason}
        else:
            tools[tool] = schema
    return {"version": 1, "tools": tools, "untyped": untyped}


def document_text() -> str:
    return json.dumps(generate(), indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    from scripts.generate_mcp_call_map import _test_environment

    _test_environment()
    if argv[1:] == ["--write"]:
        SCHEMAS_PATH.write_text(document_text(), encoding="utf-8")
        return 0
    if argv[1:] == ["--check"]:
        current = SCHEMAS_PATH.read_text(encoding="utf-8") if SCHEMAS_PATH.exists() else ""
        if current != document_text():
            print(
                f"{SCHEMAS_PATH} is stale or was edited by hand: run with --write", file=sys.stderr
            )
            return 1
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
