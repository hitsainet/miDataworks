"""The live MCP registry's text (feature 020; FR-020.19, FR-020.23; FTASKS 8.5)."""

from __future__ import annotations

import pytest

from tests.support import legacy_identifiers as li
from tests.unit.brand import scan


def test_live_registry_report_count_equals_reference() -> None:
    export, deny = li.load(), scan.policy()["deny"]
    texts = scan.mcp_texts()
    assert len([w for w, _ in texts if w.startswith("tool ")]) > 200, "the registry was not built"
    read, identifiers = scan.mcp_hits(export, deny)
    scan.record("mcp_identifiers", identifiers)
    # Reference: every text read again one by one; a tool name is never an identifier here.
    reference = sum(
        len(li.classified_hits(text, export, deny, whole=False)[0]) for _, text in texts
    )
    assert scan.record("mcp", read) == reference


def test_live_registry_text_has_no_unallowed_old_name() -> None:
    export, deny = li.load(), scan.policy()["deny"]
    scan.judge("mcp", scan.mcp_hits(export, deny)[0])


def test_a_tool_name_is_read_as_it_stands(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tool registered under an old sibling name is a finding, never a reported identifier
    (FR-020.17; the absence test in ``test_probe_run_tools`` is the other guard). Through
    ``scan.mcp_hits`` itself, the function the verdict uses, so a change of its scope is seen."""
    export, deny = li.load(), scan.policy()["deny"]
    planted = [
        ("tool dataworks_list_millm_probes", "dataworks_list_millm_probes"),
        ("tool dataworks_x", "Refuses with `mistudio_foo_bar` when unset."),
    ]
    monkeypatch.setattr(scan, "mcp_texts", lambda: planted)
    read, identifiers = scan.mcp_hits(export, deny)
    assert read == ["tool dataworks_list_millm_probes: millm"]
    assert identifiers == ["tool dataworks_x: mistudio"]


def test_the_howto_text_of_every_topic_is_a_surface() -> None:
    """FTID 7.5 names the text ``dataworks_howto`` returns for each topic as MCP text. It was not
    read before 2026-11-11 (found while preparing control M44b: an old name written into a topic
    would have passed the registry scan). The topics are the ones the live tool answers, each
    read by calling the tool."""
    texts = scan.mcp_texts()
    howto = {where.removeprefix("howto ") for where, _ in texts if where.startswith("howto ")}
    from src.mcp_server.tools.core import HOWTO

    assert howto == set(HOWTO) and len(howto) >= 10
    for topic, text in HOWTO.items():
        assert (f"howto {topic}", text) in texts, topic
