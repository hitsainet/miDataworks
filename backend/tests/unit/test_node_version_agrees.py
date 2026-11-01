"""Every Node version the repo names agrees, and it is the suite's Node 22 LTS (decision D34).

Node 20 reached end-of-life on 2026-04-30; the suite moved to Node 22 on 2026-10-17. CI
(``actions/setup-node``) and the image build (``FROM node:``) must use the same major, or the image
ships with a toolchain CI never tested. The references are found by scanning the files, not by a
hand-kept list, so a new workflow or Dockerfile is covered the day it lands.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
EXPECTED_MAJOR = "22"

NODE_VERSION = re.compile(r"""^\s*node-version:\s*['"]?([^'"\s#]+)""", re.MULTILINE)
SETUP_NODE = re.compile(r"uses:\s*actions/setup-node@")
FROM_NODE = re.compile(r"^\s*FROM\s+(?:--\S+\s+)*node:([^\s]+)", re.MULTILINE | re.IGNORECASE)


def _tracked(*patterns: str) -> list[Path]:
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "--", *patterns],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [REPO / line for line in out.splitlines() if line]


def _major(spec: str) -> str:
    m = re.match(r"v?(\d+)", spec)
    assert m, f"unparseable Node version {spec!r}"
    return m.group(1)


def node_references() -> list[tuple[str, str]]:
    """(where, version spec) for every Node version named in workflows, Dockerfiles and rc files."""
    refs: list[tuple[str, str]] = []
    for wf in _tracked(".github/workflows/*.yml", ".github/workflows/*.yaml"):
        text = wf.read_text()
        versions = NODE_VERSION.findall(text)
        # A setup-node step with no node-version would silently take the runner's default Node.
        assert len(versions) >= len(
            SETUP_NODE.findall(text)
        ), f"{wf.relative_to(REPO)}: a setup-node step has no node-version"
        refs += [(str(wf.relative_to(REPO)), v) for v in versions]
    for df in _tracked("*Dockerfile*", "**/*Dockerfile*"):
        refs += [(str(df.relative_to(REPO)), v) for v in FROM_NODE.findall(df.read_text())]
    for rc in _tracked(".nvmrc", "**/.nvmrc", ".node-version", "**/.node-version"):
        refs.append((str(rc.relative_to(REPO)), rc.read_text().strip()))
    return refs


def test_node_references_are_found():
    # The scan must see both CI and the image; an empty scan would make the next test vacuous.
    where = {w for w, _ in node_references()}
    assert any(w.startswith(".github/workflows/") for w in where), where
    assert any("Dockerfile" in w for w in where), where


def test_every_node_reference_is_node_22():
    refs = node_references()
    wrong = [(w, v) for w, v in refs if _major(v) != EXPECTED_MAJOR]
    assert not wrong, f"Node references not on {EXPECTED_MAJOR}: {wrong} (all: {refs})"
