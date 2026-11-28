"""Every third-party import of ``src/rules/`` is a pinned requirement of the image (D38-4).

The contract library runs in the image, which installs ``requirements.txt`` with
``constraints.txt``; a module the dev venv happens to hold but no requirement names imports locally
and fails in production. Read from the syntax tree of the live package and the installed
distributions, not a hand-kept list.
"""

from __future__ import annotations

import ast
import importlib.metadata
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3]
#: A distribution that ships inside another's pin (pydantic pins its own core exactly).
PART_OF = {"pydantic-core": "pydantic"}


def _pins(name: str) -> set[str]:
    out = set()
    for line in (BACKEND / name).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.add(re.split(r"[=\[<>~! ]", line)[0].lower().replace("_", "-"))
    return out


def rule_distributions() -> set[str]:
    owners = importlib.metadata.packages_distributions()
    found: set[str] = set()
    for path in sorted((BACKEND / "src" / "rules").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top in sys.stdlib_module_names or top == "src":
                    continue
                dists = owners.get(top)
                assert dists, f"{path.name} imports {top}, which no installed distribution provides"
                found |= {d.lower().replace("_", "-") for d in dists}
    return found


def test_every_rules_import_is_pinned_for_the_image() -> None:
    found = rule_distributions()
    assert {"rfc8785", "google-re2"} <= found, found  # the walk sees the two 016 pins
    requirements, constraints = _pins("requirements.txt"), _pins("constraints.txt")
    missing = {
        d: PART_OF.get(d, d)
        for d in found
        if PART_OF.get(d, d) not in requirements or d not in constraints
    }
    assert missing == {}
