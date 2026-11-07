"""No shipped file claims a feature is missing from the build.

Every screen, guided step and export target is built (2026-10-17). Fallbacks that still said "This
screen is not built yet", "The <step> step is not built yet" or "is not available in this build"
were unreachable, and if ever reached they would have lied: the cause would be a fault, not a
missing feature. This scans every shipped source file (backend ``src/``, frontend ``src/``
outside tests) for that copy. The self-tests prove the scan reads the trees and bites.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TREES = (ROOT / "backend" / "src", ROOT / "frontend" / "src")
SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".html", ".md"}
PHRASES = re.compile(
    r"not\s+built\s+yet|(?:not\s+available|not\s+installed)\s+in\s+this\s+build", re.I
)


def shipped(tree: Path) -> list[Path]:
    out = []
    for p in tree.rglob("*"):
        if not p.is_file() or p.suffix not in SUFFIXES or "node_modules" in p.parts:
            continue
        if ".test." in p.name or "__pycache__" in p.parts or "test" in p.relative_to(tree).parts:
            continue
        out.append(p)
    return out


def offenders(files: list[Path]) -> list[str]:
    hits = []
    for p in files:
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if PHRASES.search(line):
                hits.append(f"{p.relative_to(ROOT)}:{n}: {line.strip()}")
    return hits


def test_no_shipped_file_says_a_feature_is_not_built() -> None:
    assert offenders([f for tree in TREES for f in shipped(tree)]) == []


def test_the_scan_reads_both_trees() -> None:
    for tree, marker in ((TREES[0], "main.py"), (TREES[1], "App.tsx")):
        files = shipped(tree)
        assert len(files) > 50 and any(f.name == marker for f in files), tree


def test_the_scan_bites(tmp_path: Path) -> None:
    planted = tmp_path / "Panel.tsx"
    planted.write_text('const t = "This screen is NOT built yet";\n', encoding="utf-8")
    other = tmp_path / "svc.py"
    other.write_text('raise X("is not available in this build")\n', encoding="utf-8")
    third = tmp_path / "checks.py"
    third.write_text('f"feature {o} is not installed in this build"\n', encoding="utf-8")
    hits = [h for p in (planted, other, third) for h in offenders_at(p, tmp_path)]
    assert len(hits) == 3


def offenders_at(p: Path, root: Path) -> list[str]:
    return [
        f"{p.relative_to(root)}:{n}"
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if PHRASES.search(line)
    ]
