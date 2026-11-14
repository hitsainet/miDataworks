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

Usage: ``python3 manual/scripts/check_published.py [REPO_ROOT] [--brand]``
"""

from __future__ import annotations

import json
import re
import subprocess
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


#: Feature 020 (CF-2, implementation default ID-3): the public variant of the rebrand export ships on the mirror
#: (DOCS_KEPT) although it is not a manual page, so it is scanned like one when present.
PUBLIC_EXPORT = Path("docs") / "legacy-identifiers.public.md"


def published_files(root: Path) -> list[Path]:
    policy = json.loads((root / POLICY).read_text())
    files = [Path("docs") / page["source"] for page in policy["pages"]]
    if (root / PUBLIC_EXPORT).is_file():
        files.append(PUBLIC_EXPORT)
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


# --- feature 020: the brand pass (--brand) ----------------------------------------------------
#
# The same allow and deny logic as the backend's brand guard, read from the same two files and
# nothing else: brand-guard.json ({"mode", "deny"}) and the rebrand export (allowances: register
# entries in force, env, repo and host names in transition, active guard rows; denies: the token
# plus active deny guards).
#
# Scope (FPRD Q4, PD-7), the twin of tests/support/legacy_identifiers.classified_hits: prose is
# enforced; a backtick code span holding one identifier token is reported only, unless the token
# IS the old name in a display form (any capital). backend/tests/unit/brand/scope_cases.json holds
# the three implementations to the same answers.
#
# Mode (suite decision R23, 2026-11-11): the policy's mode in the private tree; REPORT on the
# public mirror snapshot - proved by its commit, see mirror_snapshot() - where only the public
# export exists (it withholds the old host names and the private repository names). Anywhere
# else a missing full export is an error, never a fallback. The mirror's verify step does not
# pass --brand; the mirror's own Manual CI does (the workflow file is shared).

PHASES = ("P0", "P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8")
FULL_EXPORT = Path("docs") / "legacy-identifiers.md"
PUBLIC_EXPORT = Path("docs") / "legacy-identifiers.public.md"
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*(?:/v?[0-9]+)?")
CODE_SPAN = re.compile(r"`([^`\n]+)`")
_SOURCE_COMMIT = re.compile(r"^Source-Commit: [0-9a-f]{40}$", re.M)


def mirror_snapshot(root: Path) -> bool:
    """True only when ``root``'s HEAD is the snapshot sync-to-clean.yml publishes: a parentless
    commit with the ``Source-Commit: <sha>`` and ``Private-CI: passed`` trailers its "Build the
    orphan snapshot" step writes (the ones the mirror's image build requires). The standard-library
    twin of backend/tests/support/source_tree.py's predicate; test_manual_publish.py holds the two
    to the same answers."""
    try:
        parents = subprocess.run(
            ["git", "rev-list", "--parents", "-n", "1", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        trailers = subprocess.run(
            ["git", "log", "-1", "--format=%(trailers:only,unfold)", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, NotADirectoryError):
        return False
    if parents.returncode != 0 or trailers.returncode != 0:
        return False
    if len(parents.stdout.split()) != 1:
        return False
    lines = trailers.stdout.splitlines()
    return "Private-CI: passed" in lines and bool(_SOURCE_COMMIT.search(trailers.stdout))


def _export(root: Path) -> tuple[dict, list[dict]]:
    path = root / FULL_EXPORT
    if not path.is_file():
        if mirror_snapshot(root) and (root / PUBLIC_EXPORT).is_file():
            path = root / PUBLIC_EXPORT
        else:
            raise SystemExit(
                f"check_published --brand: {FULL_EXPORT} is missing and {root} is not the mirror "
                "snapshot; the brand rules come from the full export only"
            )
    text = path.read_text(encoding="utf-8")
    block = re.search(r"^```jsonl\n(.*?)^```$", text, flags=re.M | re.S)
    if not block:
        raise SystemExit(f"check_published --brand: {path.name} has no jsonl block")
    rows = [json.loads(line) for line in block.group(1).splitlines() if line.strip()]
    return rows[0], rows[1:]


def _active(header: dict, row: dict) -> bool:
    phase = PHASES.index(header["phase"])
    start, end = row.get("from_phase"), row.get("until_phase")
    return (start is None or phase >= PHASES.index(start)) and (
        end is None or phase < PHASES.index(end)
    )


def brand_rules(root: Path) -> tuple[str, list[re.Pattern[str]], list[re.Pattern[str]]]:
    guard = json.loads((root / "brand-guard.json").read_text())
    header, records = _export(root)
    allow: list[re.Pattern[str]] = []
    deny: list[re.Pattern[str]] = [re.compile(guard["deny"])]
    for r in records:
        section = r["section"]
        if section == "entry" and r["status"] in ("frozen", "scheduled"):
            allow.append(re.compile(r["pattern"]))
            allow.extend(re.compile(re.escape(lit)) for lit in r["literals"])
        elif section in ("env", "repo") and r["status"] == "transition":
            allow.append(re.compile(re.escape(r["old"])))
        elif section == "host" and r["status"] == "transition":
            # A whole host name in transition, never the start of a longer label.
            allow.append(
                re.compile(r"(?<![A-Za-z0-9_.-])" + re.escape(r["old"]) + r"(?![A-Za-z0-9_-])")
            )
        elif section == "guard" and _active(header, r):
            (allow if r["effect"] == "allow" else deny).append(re.compile(r["pattern"]))
    mode = str(guard["mode"])
    if mode == "enforce" and mirror_snapshot(root):
        mode = "report on the public mirror, R23"
    return mode, allow, deny


def _is_identifier(token: str, hit: str) -> bool:
    if not IDENTIFIER.fullmatch(token):
        return False
    return not (token == hit and hit != hit.lower())


def classify(
    line: str, allow: list[re.Pattern[str]], deny: list[re.Pattern[str]]
) -> tuple[list[str], list[str]]:
    """``(read, identifier)``: the unallowed hits of one line of prose, split by scope."""
    spans = [m.span() for p in allow for m in p.finditer(line) if m.end() > m.start()]
    code = [(m.start(1), m.end(1)) for m in CODE_SPAN.finditer(line)]
    found: dict[tuple[int, int], str] = {}
    for pattern in deny:
        for match in pattern.finditer(line):
            s, e = match.span()
            if not any(a <= s and e <= b for a, b in spans):
                found.setdefault((s, e), match.group(0))
    read: list[str] = []
    identifier: list[str] = []
    for s, e in sorted(found):
        hit = found[(s, e)]
        token = next((line[a:b] for a, b in code if a <= s and e <= b), None)
        (identifier if token is not None and _is_identifier(token, hit) else read).append(hit)
    return read, identifier


def brand_scan(root: Path) -> tuple[str, list[str], list[str]]:
    """``(mode, read hits, identifier hits)`` over the published set."""
    mode, allow, deny = brand_rules(root)
    hits: list[str] = []
    identifiers: list[str] = []
    for rel in published_files(root):
        path = root / rel
        if not path.is_file() or rel.name.startswith("legacy-identifiers"):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            read, identifier = classify(line, allow, deny)
            hits.extend(f"{rel}:{number}: {hit}" for hit in read)
            identifiers.extend(f"{rel}:{number}: {hit}" for hit in identifier)
    return mode, hits, identifiers


def main(argv: list[str]) -> int:
    brand = "--brand" in argv
    argv = [a for a in argv if a != "--brand"]
    root = Path(argv[1] if len(argv) > 1 else ".").resolve()
    if brand:
        mode, hits, identifiers = brand_scan(root)
        print(
            f"check_published --brand ({mode}): {len(hits)} unallowed old name(s); "
            f"{len(identifiers)} identifier hit(s), reported only"
        )
        for hit in hits:
            print(f"  - {hit}")
        for hit in identifiers:
            print(f"  ~ {hit} (identifier)")
        if mode == "enforce" and hits:
            return 1
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
