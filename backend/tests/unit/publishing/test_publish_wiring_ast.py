"""The CALLS that ship, asserted by walking the AST (008 FTID section 8; FTASKS 6.6, 15.1, 17.9).

The 033 lesson: every first-time mutation survivor was the CALLER of a well-tested helper. A text
search matches comments; the AST sees only calls.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[3] / "src"


def _tree(rel: str) -> ast.Module:
    return ast.parse((SRC / rel).read_text())


def _function(rel: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(_tree(rel)):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {rel}")


def _calls(node: ast.AST) -> list[str]:
    names: list[str] = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            if isinstance(n.func, ast.Name):
                names.append(n.func.id)
            elif isinstance(n.func, ast.Attribute):
                names.append(n.func.attr)
    return names


def _imports(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            found |= {a.name for a in n.names}
            found.add(n.module or "")
        elif isinstance(n, ast.Import):
            found |= {a.name for a in n.names}
    return found


def test_the_publish_job_calls_every_decision_it_ships() -> None:
    calls = _calls(_function("services/publishing/publish_job.py", "run_publish"))
    for name in (
        "whoami_scope",
        "assemble",
        "evaluate_checks",
        "push_allowed",
        "plan",
        "create_private_repo",
        "verify_commit",
        "set_public",
        "is_private",
        "with_publication",
        "manifest_bytes",
        "build_document",
    ):
        assert name in calls, name
    verify = _calls(_function("services/publishing/publish_job.py", "verify_commit"))
    assert "compare" in verify and "check_configs" in verify


def test_manifests_are_validated_against_the_committed_file_before_they_are_written() -> None:
    assert "validate_against_file" in _calls(
        _function("services/publishing/manifest_builder.py", "build_document")
    )
    assert "validate_against_file" in _calls(
        _function("services/publishing/manifest_builder.py", "manifest_bytes")
    )
    assert "manifest_bytes" in _calls(_function("services/exports/export_service.py", "_run"))
    assert "atomic_write_bytes" in _calls(_function("services/exports/export_service.py", "_run"))


def test_the_service_checks_authorization_before_creating_anything() -> None:
    fn = _function("services/publishing/publish_service.py", "request_publish")
    calls = _calls(fn)
    assert calls.index("check_authorization") < calls.index("add")


def test_the_routes_use_the_single_entry_point() -> None:
    assert "request_publish" in _calls(_function("api/v1/endpoints/publishing.py", "_create"))


def test_checks_call_004s_two_functions() -> None:
    calls = _calls(_function("services/publishing/checks.py", "gather_curation_findings"))
    assert calls.count("check_leakage") == 1 and calls.count("evaluate_warnings") == 1
    assert "gather_curation_findings" in _calls(
        _function("services/publishing/check_inputs.py", "assemble")
    )


def test_exports_call_004s_validator_before_writing() -> None:
    calls = _calls(_function("services/exports/export_service.py", "_run"))
    assert calls.index("_validate_with_004") < calls.index("write_split")
    assert "validate_trl" in _calls(
        _function("services/exports/export_service.py", "_validate_with_004")
    )


def test_only_the_publish_worker_resolves_the_token() -> None:
    """FR-008.16, 008.17: no API module and no service calls the resolver or whoami."""
    offenders: list[str] = []
    for path in (SRC / "api").rglob("*.py"):
        tree = ast.parse(path.read_text())
        if {"resolve_hf_token", "whoami_scope", "HubClient"} & (set(_calls(tree)) | _imports(tree)):
            offenders.append(str(path.relative_to(SRC)))
    for folder in ("services", "hub"):
        for path in (SRC / folder).rglob("*.py"):
            tree = ast.parse(path.read_text())
            if "resolve_hf_token" in set(_calls(tree)) | _imports(tree):
                offenders.append(str(path.relative_to(SRC)))
    assert offenders == []
    assert "resolve_hf_token" in _calls(_function("workers/publish_tasks.py", "_hub_client"))


def test_hfapi_is_built_only_in_the_hub_client() -> None:
    offenders = [
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if p != SRC / "hub" / "hub_client.py" and "HfApi" in _imports(ast.parse(p.read_text()))
    ]
    assert offenders == []


def test_the_publish_settings_are_read_where_they_are_used() -> None:
    """FTASKS 16.2: the per-file limit reaches the plan; the retries reach the client."""
    publish = _function("services/publishing/publish_job.py", "run_publish")
    assert any(
        isinstance(n, ast.Attribute) and n.attr == "publish_max_file_bytes"
        for n in ast.walk(publish)
    )
    client = _function("workers/publish_tasks.py", "_hub_client")
    attrs = {n.attr for n in ast.walk(client) if isinstance(n, ast.Attribute)}
    assert {"publish_hub_retries", "publish_hub_backoff_s"} <= attrs
