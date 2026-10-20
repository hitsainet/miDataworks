#!/usr/bin/env python3
"""Scan what the public manual publishes for content that must stay private.

The published set is: every docs/ page named in ``manual/publish-allowlist.json``, and every text
file of ``manual/`` (the hand-written pages, the site config and scripts). Each line is checked
against the policy's ``forbidden`` patterns (internal hostnames, private addresses, the private
planning tree, tokens, ...). Exit 1 names every hit as ``path:line: what``.

Standard library only, so it runs unchanged in three places that must agree:
- the private repository's Manual CI (``.github/workflows/manual-ci.yml``);
- the mirror sync's verify step, against the PUBLISHED clone (``sync-to-clean.yml``);
- ``backend/tests/unit/test_manual_publish.py``, which also proves the scan bites.

The policy file itself is not scanned: it holds the patterns as text.

Usage: ``python3 manual/scripts/check_published.py [REPO_ROOT]``
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

POLICY = Path("manual") / "publish-allowlist.json"
TEXT_SUFFIXES = {
    ".md",
    ".mdx",
    ".ts",
    ".tsx",
    ".js",
    ".mjs",
    ".json",
    ".css",
    ".py",
    ".txt",
    ".yml",
}
SKIP_PARTS = {"node_modules", "build", ".docusaurus", ".cache-loader"}
SKIP_FILES = {"package-lock.json", "publish-allowlist.json"}
# Generated from docs/ by sync-docs.mjs; their sources are scanned instead.
GENERATED = {Path("manual/docs/guide"), Path("manual/docs/reference")}


def published_files(root: Path) -> list[Path]:
    policy = json.loads((root / POLICY).read_text())
    files = [Path("docs") / page["source"] for page in policy["pages"]]
    for path in sorted((root / "manual").rglob("*")):
        rel = path.relative_to(root)
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        if SKIP_PARTS & set(rel.parts) or rel.name in SKIP_FILES:
            continue
        if any(g in rel.parents for g in GENERATED):
            continue
        files.append(rel)
    return files


def scan(root: Path) -> list[str]:
    policy = json.loads((root / POLICY).read_text())
    rules = [(f["name"], re.compile(f["pattern"])) for f in policy["forbidden"]]
    problems: list[str] = []
    for rel in published_files(root):
        path = root / rel
        if not path.is_file():
            problems.append(f"{rel}: allow-listed for publication but missing")
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for name, rule in rules:
                if rule.search(line):
                    problems.append(f"{rel}:{number}: {name} must not be published")
    return problems


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else ".").resolve()
    problems = scan(root)
    if problems:
        print(f"check_published: {len(problems)} problem(s) in the published manual:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"check_published: {len(published_files(root))} published files are clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
