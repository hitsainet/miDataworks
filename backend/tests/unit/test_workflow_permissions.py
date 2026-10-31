"""CodeQL hygiene that a test can hold (security alerts record 2026-10-17).

- Every GitHub Actions job runs with an explicit, least-privilege token: a workflow-level
  ``permissions`` block, or one on the job (CodeQL ``actions/missing-workflow-permissions``,
  mirror alert 1). Read from the parsed workflow files, the documents Actions itself reads.
- No weak hash is used as a security primitive: a SHA-1 or MD5 call must say
  ``usedforsecurity=False`` (git blob ids are SHA-1 by protocol). Read from the parsed AST of every
  backend module and test (CodeQL ``py/weak-sensitive-data-hashing``, mirror alert 8).
"""

from __future__ import annotations

import ast
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
WORKFLOWS = sorted((REPO / ".github" / "workflows").glob("*.yml"))
BACKEND = REPO / "backend"
WEAK = {"sha1", "md5"}


def test_the_workflows_are_found() -> None:
    assert len(WORKFLOWS) >= 5, WORKFLOWS


def test_every_job_has_an_explicit_token_scope() -> None:
    for path in WORKFLOWS:
        doc = yaml.safe_load(path.read_text())
        top = doc.get("permissions")
        for name, job in doc["jobs"].items():
            scope = job.get("permissions", top)
            assert scope is not None, f"{path.name}:{name} runs with the default token"
            assert scope not in ("write-all", "read-all"), f"{path.name}:{name}: {scope}"


def test_the_image_workflow_defaults_to_read_only() -> None:
    doc = yaml.safe_load((REPO / ".github" / "workflows" / "docker-images.yml").read_text())
    assert doc["permissions"] == {"contents": "read"}
    assert "permissions" not in doc["jobs"]["detect"]  # detect inherits read-only


def _weak_calls(tree: ast.AST) -> list[int]:
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        new = (
            name == "new"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and str(node.args[0].value).lower() in WEAK
        )
        if name not in WEAK and not new:
            continue
        flagged = any(
            k.arg == "usedforsecurity"
            and isinstance(k.value, ast.Constant)
            and k.value.value is False
            for k in node.keywords
        )
        if not flagged:
            out.append(node.lineno)
    return out


def test_no_weak_hash_without_usedforsecurity_false() -> None:
    # test_suite_reports_its_totals.py writes and deletes test_zz_totals_probe_<pid>.py while
    # other workers run; reading one that vanished failed this test (seen 2026-10-18).
    files = [
        p
        for p in [*(BACKEND / "src").rglob("*.py"), *(BACKEND / "tests").rglob("*.py")]
        if not p.name.startswith("test_zz_totals_probe_")
    ]
    assert len(files) > 300
    offenders = {
        str(p.relative_to(REPO)): lines
        for p in files
        if (lines := _weak_calls(ast.parse(p.read_text(), str(p))))
    }
    assert offenders == {}


def test_the_weak_hash_guard_bites() -> None:
    assert _weak_calls(ast.parse("import hashlib\nhashlib.sha1(b'x').hexdigest()")) == [2]
    assert _weak_calls(ast.parse("import hashlib\nhashlib.new('md5', b'x')")) == [2]
    assert _weak_calls(ast.parse("import hashlib\nhashlib.sha1(b'x', usedforsecurity=False)")) == []
