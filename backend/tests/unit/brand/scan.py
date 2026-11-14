"""The brand guard's scanners (feature 020; FTID §7.5).

Policy: ``brand-guard.json`` holds ONLY ``mode`` (``report`` until P4 closes, then ``enforce``) and
the ``deny`` token. Every allowance and every phase rule comes from the export
(``tests/support/legacy_identifiers.py``): register entries in force, env and repo names in
transition, and the active ``guard`` rows. No list is kept here (control M47b).

Discovery is ``git ls-files`` with the export excluded by a GLOB pathspec (CF-20: the export lists
every old name and must never count against the guard; the non-glob ``:!**/…`` form misses
root-level files, control M16f). Never ``rglob``: it would see untracked and ignored files.

Each scanner prints its hits in both modes and records its count in ``brand-guard-report.json``.

Scope (FPRD Q4, PD-7): read surfaces are enforced, identifiers are reported. The split is
``legacy_identifiers.classified_hits``: an identifier is a whole literal shaped like one, or the
same inside a backtick code span; manifest keys and values and MCP tool names are read as they
stand. Identifier hits are printed and counted under ``<scanner>_identifiers`` in every mode.

Mode (suite decision R23): the mode a scanner APPLIES is the policy's, except on the public mirror
snapshot, where it is ``report`` (the mirror's export withholds allowances this tree has; the
private CI is the gate). See :func:`applied_mode`.
"""

from __future__ import annotations

import ast
import json
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from tests.support import legacy_identifiers as li

REPO = li.REPO
POLICY = REPO / "brand-guard.json"
REPORT = REPO / "brand-guard-report.json"
EXPORT_EXCLUDE = ":(exclude,glob)**/docs/legacy-identifiers*.md"


def policy() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(POLICY.read_text())
    return data


def tracked(*pathspecs: str, root: Path = REPO, exclude: str = EXPORT_EXCLUDE) -> list[Path]:
    output = subprocess.run(
        ["git", "ls-files", "-z", "--", *pathspecs, exclude],
        cwd=root,
        capture_output=True,
        check=True,
    ).stdout
    return [root / raw.decode() for raw in output.split(b"\0") if raw]


def record(scanner: str, hits: list[str]) -> int:
    """Print the hits, write the count into the report, and return it."""
    for hit in hits:
        print(f"brand-guard[{scanner}] {hit}")
    try:
        report = json.loads(REPORT.read_text())
    except (OSError, ValueError):
        report = {}
    report[scanner] = len(hits)
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return len(hits)


def applied_mode(policy_mode: str | None = None, root: Path = REPO) -> str:
    """The mode the scanners apply in ``root`` (R23): the policy's mode in the private tree,
    ``report`` on the public mirror snapshot whatever the policy says."""
    from tests.support.source_tree import mirror_snapshot

    mode = str(policy_mode if policy_mode is not None else policy()["mode"])
    if mode == "enforce" and mirror_snapshot(root):
        return "report"
    return mode


def judge(scanner: str, hits: list[str], mode: str | None = None, root: Path = REPO) -> int:
    """Report mode records; enforce mode also fails on any unallowed hit. ``mode`` is the
    policy's mode (read from ``brand-guard.json`` when not given); what is applied follows
    :func:`applied_mode`."""
    count = record(scanner, hits)
    policy_mode = str(mode if mode is not None else policy()["mode"])
    applied = applied_mode(policy_mode, root)
    if applied != policy_mode:
        print(f"brand-guard[{scanner}] {count} hit(s): report mode on the public mirror (R23)")
    if applied == "enforce":
        assert hits == [], f"{scanner}: {count} unallowed old name(s): {hits[:20]}"
    return count


def split_hits(
    texts: Iterable[tuple[str, str]],
    export: li.Export,
    deny: str,
    *,
    whole: bool,
    code_spans: bool = True,
) -> tuple[list[str], list[str]]:
    """``(read, identifier)`` hits of a surface, each as ``"<where>: <hit>"``."""
    read: list[str] = []
    identifier: list[str] = []
    for where, text in texts:
        r, i = li.classified_hits(text, export, deny, whole=whole, code_spans=code_spans)
        read.extend(f"{where}: {hit}" for hit in r)
        identifier.extend(f"{where}: {hit}" for hit in i)
    return read, identifier


def backend_hits(export: li.Export, deny: str) -> tuple[list[str], list[str]]:
    """Backend string literals: a whole literal or a code span may be an identifier."""
    return split_hits(backend_literals(backend_files()), export, deny, whole=True)


def mcp_hits(export: li.Export, deny: str) -> tuple[list[str], list[str]]:
    """The live MCP registry: a tool NAME is read as it stands; a code span may be an identifier."""
    return split_hits(mcp_texts(), export, deny, whole=False)


def document_hits(export: li.Export, deny: str) -> tuple[list[str], list[str]]:
    """README.md, AGENT.md, .env.example: prose; a code span may be an identifier."""
    return split_hits(documents(), export, deny, whole=False)


def manifest_hits(export: li.Export, deny: str) -> list[str]:
    """Manifest keys and values: all read (a key IS the configuration name)."""
    return split_hits(manifests(), export, deny, whole=False, code_spans=False)[0]


# --- surfaces -----------------------------------------------------------------------------------


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def backend_literals(paths: list[Path]) -> list[tuple[str, str]]:
    """Every ``str`` constant under backend/src that is not a docstring (f-string parts too)."""
    out = []
    for path in paths:
        tree = ast.parse(path.read_text(), str(path))
        skip = _docstring_ids(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in skip
            ):
                out.append((f"{path.relative_to(REPO)}:{node.lineno}", node.value))
    return out


def backend_files() -> list[Path]:
    return tracked("backend/src/**/*.py", "backend/src/*.py")


def documents() -> list[tuple[str, str]]:
    return [
        (str(p.relative_to(REPO)), p.read_text(encoding="utf-8"))
        for p in tracked("README.md", "AGENT.md", ".env.example")
    ]


def _yaml_strings(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _yaml_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _yaml_strings(item)
    elif value is not None:
        yield str(value)


def manifests(paths: list[Path] | None = None) -> list[tuple[str, str]]:
    """Parsed keys and values of k8s/**/*.yaml and the workflows, never comments."""
    import yaml

    files = paths if paths is not None else tracked("k8s/**/*.yaml", ".github/workflows/*.yml")
    out = []
    for path in files:
        for doc in yaml.safe_load_all(path.read_text()):
            for text in _yaml_strings(doc):
                out.append((str(path.relative_to(REPO)), text))
    return out


def mcp_texts() -> list[tuple[str, str]]:
    """The LIVE registry: tool names, descriptions, every schema description, the instructions,
    and the text ``dataworks_howto`` answers for each topic it lists (the tool is called)."""
    import asyncio

    from src.mcp_server.config import MCPSettings
    from src.mcp_server.server import build_server

    mcp, _ = build_server(MCPSettings(auth_token="x" * 32))
    tools = asyncio.run(mcp.list_tools())
    out: list[tuple[str, str]] = [("instructions", str(getattr(mcp, "instructions", "") or ""))]

    def descriptions(schema: Any, where: str) -> None:
        if isinstance(schema, dict):
            for key, value in schema.items():
                if key == "description" and isinstance(value, str):
                    out.append((where, value))
                else:
                    descriptions(value, where)
        elif isinstance(schema, list):
            for item in schema:
                descriptions(item, where)

    for tool in tools:
        out.append((f"tool {tool.name}", tool.name))
        out.append((f"tool {tool.name}", tool.description or ""))
        descriptions(
            getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None),
            f"tool {tool.name}",
        )

    from tests.support.mcp_harness import tool_context

    def howto(arguments: dict[str, str]) -> dict[str, Any]:
        answer: dict[str, Any] = asyncio.run(
            mcp._tool_manager.call_tool(  # noqa: SLF001 - the server's own dispatch
                "dataworks_howto", arguments, tool_context(mcp)
            )
        )
        return answer

    for topic in howto({})["topics"]:
        out.append((f"howto {topic}", str(howto({"topic": topic})["text"])))
    return out
