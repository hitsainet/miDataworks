"""README.md, AGENT.md and .env.example (feature 020; FR-020.11, FR-020.24; FTASKS 8.5)."""

from __future__ import annotations

import copy
import re
import subprocess

import pytest

from tests.support import legacy_identifiers as li
from tests.support.source_tree import mirror_snapshot, stripped_by_mirror
from tests.unit.brand import scan

DOCUMENTS = {"README.md", "AGENT.md", ".env.example"}


def _reference(export: li.Export, deny: str) -> int:
    """``git grep`` over the same files, line by line, minus allowed spans."""
    pattern = deny.removeprefix("(?i)")
    output = subprocess.run(
        ["git", "grep", "-h", "-i", "-E", pattern, "--", "README.md", "AGENT.md", ".env.example"],
        cwd=scan.REPO,
        capture_output=True,
        text=True,
    ).stdout
    assert re.compile(deny)  # the same token
    return sum(
        len(li.classified_hits(line, export, deny, whole=False)[0]) for line in output.splitlines()
    )


def test_documents_report_count_equals_reference_grep() -> None:
    export, deny = li.load(), scan.policy()["deny"]
    docs = scan.documents()
    # AGENT.md is stripped from the mirror: expected there, and only there, without it.
    on_mirror = mirror_snapshot()
    expected = {d for d in DOCUMENTS if not (on_mirror and stripped_by_mirror(d))}
    assert {d for d, _ in docs} == expected
    read, identifiers = scan.document_hits(export, deny)
    scan.record("documents_identifiers", identifiers)
    assert scan.record("documents", read) == _reference(export, deny)


def test_documents_have_no_unallowed_old_name() -> None:
    export, deny = li.load(), scan.policy()["deny"]
    scan.judge("documents", scan.document_hits(export, deny)[0])


def test_agent_md_passes_enforced_at_p5() -> None:
    """CF-22 (FR-020.24; FTASKS 18.3): from P5, ``AGENT.md`` names the suite only by its paths
    (``~/app/miStudio/...``) and PD-19's phrases, and passes the ENFORCED guard BECAUSE those paths
    match the export's ``G-STUDIO-PATH`` allow record. Control M45e: drop that record from the
    export and this goes red."""
    export, deny = li.load(), scan.policy()["deny"]
    studio_path = li.require_guard(export, "G-STUDIO-PATH")  # skips by name on the mirror
    assert li.at_least(export.header["phase"], "P5"), export.header
    assert studio_path in li.active_guards(export)
    agent_md = dict(scan.documents())["AGENT.md"]
    assert not re.search(r"(?i)\bmiSuite\b", agent_md), "AGENT.md still names miSuite"
    paths = re.findall(studio_path["pattern"], agent_md)
    assert paths, "AGENT.md names no suite path: the G-STUDIO-PATH case would pass vacuously"
    read = scan.split_hits([("AGENT.md", agent_md)], export, deny, whole=False)[0]
    scan.judge("agent_md_p5", read, mode="enforce")
    # Green because of G-STUDIO-PATH, not by luck: without the record the paths are hits.
    without = copy.deepcopy(export)
    without.records = [r for r in without.records if r.get("id") != "G-STUDIO-PATH"]
    unallowed = scan.split_hits([("AGENT.md", agent_md)], without, deny, whole=False)[0]
    assert len(unallowed) >= len(paths), unallowed


def test_a_planted_document_line_is_classified_through_the_scanner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through ``scan.document_hits``: a document is prose, so a bare identifier-shaped word is
    read; only a code span holding one identifier is reported."""
    export, deny = li.load(), scan.policy()["deny"]
    planted = [("README.md", "is_millm\nOpen miStudio.\nIt refuses with `mistudio_foo_bar`.\n")]
    monkeypatch.setattr(scan, "documents", lambda: planted)
    read, identifiers = scan.document_hits(export, deny)
    assert read == ["README.md: millm", "README.md: miStudio"]
    assert identifiers == ["README.md: mistudio"]
