"""miStudio's ``dataworks_*`` proxy calls only routes this app serves (FR-010.43; FTDD 5.8; T-54).

Reads miStudio's tool modules from ``MISTUDIO_REPO`` (default ``/home/x-sean/app/miStudio``) by
AST, never by import (ADR-001), and matches every ``dataworks_*`` tool's client call against
``app.openapi()["paths"]``. An absent checkout or an absent proxy skips loudly;
``MIDATAWORKS_REQUIRE_CROSS_REPO_CHECKS=1`` turns an absent checkout into a failure.

Also here (FTASKS 15.5): the thirteen curated tools of miStudio 034 FR-10 v1.2 exist here under the
same names, each registered and in the generated contract, or pending on its named feature.
"""

from __future__ import annotations

import ast
import asyncio
import os
import re
from pathlib import Path

import pytest

from src.main import fastapi_app
from src.mcp_server.config import MCPSettings
from src.mcp_server.contract import generate_contract
from src.mcp_server.server import build_server
from tests.support.mcp_ledger import normalise, pending_tools

MISTUDIO = Path(os.environ.get("MISTUDIO_REPO", "/home/x-sean/app/miStudio"))
VERBS = {"get", "post", "put", "patch", "delete"}

#: miStudio 034 FR-10 v1.2: the curated proxy subset (T-53: identical names on both sides).
CURATED = (
    "dataworks_list_datasets",
    "dataworks_get_version",
    "dataworks_get_version_manifest",
    "dataworks_list_detector_sets",
    "dataworks_get_detector_set",
    "dataworks_start_label_run",
    "dataworks_build_version_files",
    "dataworks_publish_version",
    "dataworks_send_detector_set",
    "dataworks_delete_version",
    "dataworks_job_status",
    "dataworks_cancel_job",
    "dataworks_get_approval_status",
)


def _skip_or_fail(message: str) -> None:
    if os.environ.get("MIDATAWORKS_REQUIRE_CROSS_REPO_CHECKS") == "1":
        pytest.fail("CROSS-REPO (required): " + message)
    pytest.skip("CROSS-REPO: " + message)


def proxy_calls(tools_dir: Path) -> dict[str, list[tuple[str, str]]]:
    """``{tool: [(METHOD, path template)]}`` for every ``dataworks_*`` function under a dir."""
    found: dict[str, list[tuple[str, str]]] = {}
    for path in sorted(tools_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in ast.walk(tree):
            if not (isinstance(func, ast.AsyncFunctionDef) and func.name.startswith("dataworks_")):
                continue
            for node in ast.walk(func):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                    continue
                if node.func.attr not in VERBS or not node.args:
                    continue
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    template = arg.value
                elif isinstance(arg, ast.JoinedStr):
                    template = "".join(
                        str(v.value) if isinstance(v, ast.Constant) else "{}" for v in arg.values
                    )
                else:
                    continue
                if template.startswith("/"):
                    found.setdefault(func.name, []).append((node.func.attr.upper(), template))
    return found


def served() -> set[tuple[str, str]]:
    return {
        (method.upper(), normalise(path))
        for path, ops in fastapi_app.openapi()["paths"].items()
        for method in ops
    }


def test_every_mistudio_proxy_call_is_served_here() -> None:
    tools_dir = MISTUDIO / "backend/src/mcp_server/tools"
    if not tools_dir.is_dir():
        _skip_or_fail(f"MISTUDIO_REPO: no miStudio checkout at {MISTUDIO}")
    calls = proxy_calls(tools_dir)
    if not calls:
        pytest.skip(
            f"CROSS-REPO: miStudio at {MISTUDIO} has no dataworks_* proxy tools yet "
            "(miStudio 034 not implemented); nothing to check"
        )
    routes = served()
    missing = []
    for tool, pairs in calls.items():
        for method, template in pairs:
            full = template if template.startswith("/api/") else "/api/v1" + template
            if (method, normalise(full)) not in routes:
                missing.append((tool, method, full))
    assert not missing, f"miStudio's proxy calls routes this app does not serve: {missing}"


def test_the_scanner_sees_a_proxy_call(tmp_path: Path) -> None:
    """A scan that finds nothing asserts nothing: prove it can see each call shape."""
    (tmp_path / "dw.py").write_text(
        "async def dataworks_get_version(version_id):\n"
        "    return await client.get(f'/versions/{version_id}')\n"
        "async def dataworks_job_status(job_id):\n"
        "    return await client.get('/jobs/' + job_id)\n"
        "async def dataworks_delete_version(version_id):\n"
        "    return await client.delete(f'/versions/{version_id}', json_body={})\n"
    )
    assert proxy_calls(tmp_path) == {
        "dataworks_get_version": [("GET", "/versions/{}")],
        "dataworks_delete_version": [("DELETE", "/versions/{}")],
    }


def test_the_curated_tools_are_registered_and_in_the_contract_or_pending() -> None:
    mcp, _ = build_server(MCPSettings(auth_token="x" * 32))
    registered = {t.name for t in asyncio.run(mcp.list_tools())}
    contract = generate_contract()
    pending = pending_tools()
    for name in CURATED:
        if name in registered:
            assert re.search(rf"^\| `{name}` \|", contract, re.M), name
        else:
            assert name in pending, f"{name} is curated by miStudio 034 FR-10 and missing here"
