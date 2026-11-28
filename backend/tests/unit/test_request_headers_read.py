"""Every inbound request header the backend reads is known, so no approval-id re-call header exists
(022 FR-022.31 AC 3; suite CHAIN_PLAN A18).

miDataworks is server-executed: it runs an approved request itself, and a caller never sends an
approval id back to make it run. An AST pass over ``backend/src`` collects every read of an INBOUND
request header (``request.headers.get(...)``, ``request.headers[...]``, a FastAPI ``Header(...)``
parameter, and a raw ASGI ``scope["headers"]`` walk) and compares it with ``KNOWN``. A new read
fails here until it is listed with its reason, so an approval re-call header cannot slip in.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"

#: (file relative to backend/src, what is read) -> why.
KNOWN: dict[tuple[str, str], str] = {
    (
        "core/agent_origin.py",
        "AGENT_HEADER",
    ): "X-Dataworks-Agent: who is calling (the gate's input)",
    ("core/agent_origin.py", "scope headers"): "the origin middleware validates X-Dataworks-Agent "
    "on every request",
    ("core/settings_pin.py", "SESSION_HEADER"): "the Settings PIN session (operator only)",
    ("core/settings_pin.py", "AGENT_HEADER"): "an agent never holds a PIN session",
    ("api/v1/endpoints/settings_pin.py", "pin.SESSION_HEADER"): "the Settings PIN session",
    ("api/internal.py", "X-Internal-Token"): "worker-to-API emit, not exposed by the ingress",
    ("api/v1/endpoints/uploads.py", "'content-length'"): "a signed upload's declared size, refused "
    "over the URL's cap before the URL is spent (G-3)",
    ("services/sources/upload_url.py", "'cf-ray'"): "a signed upload through the public proxy "
    "is refused: uploads are taken on the LAN only (G-3, operator decision 2026-11-25)",
    ("services/sources/upload_url.py", "'cf-connecting-ip'"): "the same LAN-only check",
    ("operators/data_designer/relay.py", "'authorization'"): "the Data Designer relay's nonce",
    ("operators/data_designer/relay.py", "ROW_KEY_HEADER"): "the row a relayed model call is for",
    ("runner/server.py", "'content-length'"): "the isolated runner's body size cap",
}


def _reads(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        # request.headers.get(X) / request.headers[X]
        target = None
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
        ):
            target, arg = node.func.value, node.args[0]
        elif isinstance(node, ast.Subscript):
            target, arg = node.value, node.slice
        if (
            target is not None
            and isinstance(target, ast.Attribute)
            and target.attr == "headers"
            and isinstance(target.value, ast.Name)
            and target.value.id == "request"
        ):
            found.add(ast.unparse(arg))
        # FastAPI Header(..., alias=X)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Header"
        ):
            alias = next((k.value for k in node.keywords if k.arg == "alias"), None)
            found.add(alias.value if isinstance(alias, ast.Constant) else "Header(no alias)")
        # for name, value in scope.get("headers", [])
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "scope"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "headers"
        ):
            found.add("scope headers")
    return found


def all_reads() -> tuple[set[tuple[str, str]], int]:
    reads: set[tuple[str, str]] = set()
    files = 0
    for path in sorted(SRC.rglob("*.py")):
        files += 1
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith("mcp_server/"):
            continue  # the MCP server reads no caller header; its bearer check is middleware
        reads |= {(rel, what) for what in _reads(path)}
    return reads, files


def test_every_inbound_header_read_is_known() -> None:
    reads, files = all_reads()
    assert files > 300, f"only {files} files scanned: the root is wrong"
    unknown = sorted(reads - set(KNOWN))
    assert not unknown, f"new inbound header reads, list each with its reason: {unknown}"
    stale = sorted(set(KNOWN) - reads)
    assert not stale, f"KNOWN lists reads that no longer exist: {stale}"


def test_no_approval_header_is_read() -> None:
    reads, _ = all_reads()
    assert not [r for r in reads if "approval" in r[1].lower()], reads
