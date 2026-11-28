"""The host never reads operator code as code (feature 018; FR-018.18; FTDD §9.4; FTASKS 6.11).

Operator code runs only inside the runner's WebAssembly guest. The API, the workers, the MCP server
and the runner HOST itself may move a code rule's source around as text (hash it, diff it, store it,
send it), never execute, compile, parse or import it. Checked on the AST (never on text: suite
memory ``source-scrape-guards-fail-open``), two ways:

1. **Anywhere in ``src``**, a dynamic-code call (``exec``, ``eval``, ``compile``, ``__import__``,
   ``ast.parse``, anything from ``importlib`` or ``runpy``, under any alias) whose arguments mention
   a code-rule carrier (``source``, ``canonical_utf8``, ``code_rule_documents``, ``document`` as a
   name, attribute or subscript key) is refused.
2. **In every module that handles code rules** (``CODE_RULE_MODULES``: the runner host and contract,
   the client, the code-rule services, routes, tools and tasks), no dynamic-code call at all.

Canary modules written in the same run prove the walker sees each form.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
#: Builtins that run or compile code.
DYNAMIC_BUILTINS = frozenset({"exec", "eval", "compile", "__import__"})
#: Modules whose every callable is a dynamic-code path (``ast`` only through ``parse``).
DYNAMIC_MODULES = frozenset({"importlib", "runpy"})
#: Names that carry a code rule's source on its way through the host.
CARRIERS = frozenset({"source", "canonical_utf8", "code_rule_documents", "document"})
#: Paths (relative to src, prefix match) that handle code rules: no dynamic-code call at all.
CODE_RULE_MODULES = (
    "runner/",
    "runner_contract/",
    "clients/runner_client.py",
    "services/code_rules/",
    "api/v1/endpoints/code_rules.py",
    "api/v1/endpoints/runner.py",
    "mcp_server/tools/code_rules.py",
    "workers/code_rule_tasks.py",
)


def _aliases(tree: ast.AST) -> tuple[set[str], set[str]]:
    """(names bound to a dynamic module, names bound to a dynamic function) in this module."""
    modules: set[str] = set()
    functions: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                root = a.name.split(".")[0]
                if root in DYNAMIC_MODULES or a.name == "ast":
                    modules.add(a.asname or root)
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if root in DYNAMIC_MODULES and node.module != "importlib.metadata":
                functions.update(a.asname or a.name for a in node.names)
            if node.module == "ast":
                functions.update(a.asname or a.name for a in node.names if a.name == "parse")
            if node.module == "builtins":
                functions.update(
                    a.asname or a.name for a in node.names if a.name in DYNAMIC_BUILTINS
                )
    return modules, functions


def _is_dynamic(call: ast.Call, modules: set[str], functions: set[str]) -> bool:
    f = call.func
    if isinstance(f, ast.Name):
        return f.id in DYNAMIC_BUILTINS or f.id in functions
    if isinstance(f, ast.Attribute):
        base = f.value
        while isinstance(base, ast.Attribute):
            base = base.value
        if not isinstance(base, ast.Name):
            return False
        if base.id == "builtins" and f.attr in DYNAMIC_BUILTINS:
            return True
        if base.id in modules:
            return base.id != "ast" or f.attr == "parse"
    return False


def _mentions_a_carrier(call: ast.Call) -> bool:
    for arg in [*call.args, *(k.value for k in call.keywords)]:
        for node in ast.walk(arg):
            if isinstance(node, ast.Name) and node.id in CARRIERS:
                return True
            if isinstance(node, ast.Attribute) and node.attr in CARRIERS:
                return True
            if isinstance(node, ast.Constant) and node.value in CARRIERS:
                return True
    return False


def violations(path: Path, rel: str) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules, functions = _aliases(tree)
    strict = rel.startswith(CODE_RULE_MODULES)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_dynamic(node, modules, functions):
            if strict or _mentions_a_carrier(node):
                out.append(f"{rel}:{node.lineno}")
    return out


def test_no_module_reads_code_rule_source_as_code() -> None:
    modules = sorted(SRC.rglob("*.py"))
    assert len(modules) > 300
    found = [v for p in modules for v in violations(p, p.relative_to(SRC).as_posix())]
    assert found == []


def test_the_code_rule_modules_exist() -> None:
    """The strict list names real files (an unmatched entry would silently guard nothing)."""
    present = [p.relative_to(SRC).as_posix() for p in SRC.rglob("*.py")]
    missing = [m for m in CODE_RULE_MODULES if not any(p.startswith(m) for p in present)]
    assert missing == [], missing


@pytest.mark.parametrize(
    ("rel", "body"),
    [
        ("services/x.py", "def f(doc):\n    exec(doc['source'])\n"),
        ("services/x.py", "def f(d):\n    eval(d.source)\n"),
        ("services/x.py", "def f(canonical_utf8):\n    compile(canonical_utf8, 'x', 'exec')\n"),
        ("services/x.py", "import ast\ndef f(document):\n    ast.parse(document)\n"),
        ("services/x.py", "from ast import parse as p\ndef f(source):\n    p(source)\n"),
        ("services/x.py", "import importlib as il\ndef f(source):\n    il.import_module(source)\n"),
        ("services/x.py", "from runpy import run_path\ndef f(source):\n    run_path(source)\n"),
        ("services/x.py", "import builtins\ndef f(s):\n    builtins.exec(s.canonical_utf8)\n"),
        ("services/code_rules/save.py", "def f(t):\n    exec(t)\n"),
        ("clients/runner_client.py", "import importlib\nimportlib.import_module('json')\n"),
        ("runner/host.py", "def f(x):\n    eval(x)\n"),
    ],
)
def test_a_canary_is_caught_in_the_same_run(tmp_path: Path, rel: str, body: str) -> None:
    path = tmp_path / "canary.py"
    path.write_text(body, encoding="utf-8")
    assert violations(path, rel), body


@pytest.mark.parametrize(
    "body",
    [
        "import re\ndef f(source):\n    return re.compile(source)\n",  # a regex, not code
        "import importlib\nm = importlib.import_module('src.services.x')\n",  # no carrier
        "def f(source):\n    return source.encode()\n",
    ],
)
def test_ordinary_code_is_not_a_canary(tmp_path: Path, body: str) -> None:
    path = tmp_path / "fine.py"
    path.write_text(body, encoding="utf-8")
    assert violations(path, "services/other.py") == []
