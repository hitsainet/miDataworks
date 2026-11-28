"""The brand guard's rules come from the export, keyed to its phase (feature 020; FR-020.23,
FR-020.24; CF-15, CF-21, CF-22, CF-23; FTASKS 8.2, 8.22)."""

from __future__ import annotations

import copy
import inspect

from tests.support import legacy_identifiers as li
from tests.unit.brand import scan


def at(phase: str, closed: str | None = None) -> li.Export:
    export = copy.deepcopy(li.load())
    export.header["phase"] = phase
    export.header["closed"] = closed
    return export


def hits(line: str, export: li.Export) -> list[str]:
    return li.unallowed_hits(line, export, scan.policy()["deny"])


def test_brand_guard_json_has_no_phase_rules() -> None:
    assert set(scan.policy()) == {"mode", "deny"}
    assert scan.policy()["mode"] in ("report", "enforce")


def test_mode_is_enforce_exactly_when_closed_is_p4_or_later() -> None:
    """CF-21: enforce keys to the header's ``closed``, never to ``phase`` (control M45f)."""
    export = li.load()
    expected = "enforce" if li.enforce_mode_expected(export) else "report"
    assert scan.policy()["mode"] == expected
    assert li.enforce_mode_expected(at("P4", "P3")) is False
    assert li.enforce_mode_expected(at("P4", "P4")) is True


def test_suite_phrase_p4_denies_studio_allows_misuite() -> None:
    p4 = at("P4", "P3")
    assert hits("Open miStudio to train it.", p4) == ["miStudio"]
    assert hits("part of the miStudio suite", p4) == ["miStudio"], "the suite phrase waits for P5"
    assert hits("miSuite runs the apps", p4) == [], "miSuite is denied only from P5"


def test_suite_phrase_p5_allows_the_three_phrases_and_denies_the_rest() -> None:
    p5 = at("P5", "P4")
    for phrase in (
        "This is part of the miStudio suite.",
        "Welcome to the miStudio suite.",
        "miStudio: The Mechanistic Interpretability Suite",
    ):
        assert hits(phrase, p5) == [], phrase
    assert hits("Open miStudio to train it.", p5) == ["miStudio"]


def test_suite_phrase_misuite_plant_allowed_at_p4_found_at_p5() -> None:
    """CF-23: a planted "miSuite" passes under a P4 header and is a finding under P5 (M45d)."""
    assert hits("see the miSuite tracker", at("P4", "P3")) == []
    assert hits("see the miSuite tracker", at("P5", "P4")) == ["miSuite"]


def test_suite_phrase_studio_path_allowed_from_p5() -> None:
    """CF-22: the suite's paths after the folder move (G-STUDIO-PATH, control M45e)."""
    li.require_guard(li.load(), "G-STUDIO-PATH")
    line = "cd ~/app/miStudio/miDataworks; memory -home-x-sean-app-miStudio-miDataworks"
    assert hits(line, at("P5", "P4")) == []
    assert hits(line, at("P4", "P3")) != []


def test_repo_allowances_come_only_from_transition_records() -> None:
    """No hand-kept old-repository list in the scanner (control M47b); the export's ``repo``
    records are the only source, so a retired record allows nothing."""
    source = inspect.getsource(scan)
    assert "Onegaishimas" not in source and "hitsainet/" not in source
    export = li.load()
    # Every repo record the loaded export holds: all four pairs here; on the mirror snapshot the
    # public variant's two (the private organisation's are withheld, CF-2), so the rule still runs
    # there instead of tripping over a record the mirror cannot carry.
    olds = [r["old"] for r in export.section("repo")]
    assert olds, "the export has no repo records: the rule would pass vacuously"
    if export.variant == "full":
        assert li.OLD_PRIVATE_WORKBENCH in olds
    # The record decides, whatever the committed status (P7 retires all five, CF-13): in
    # transition it allows its slug, retired it allows nothing, and the committed export answers
    # by its own record.
    as_status = {}
    for status in ("transition", "retired"):
        as_status[status] = copy.deepcopy(export)
        for record in as_status[status].section("repo"):
            record["status"] = status
    for record in export.section("repo"):
        old = record["old"]
        line = f"pushed to {old}"
        assert hits(line, as_status["transition"]) == [], old
        assert hits(line, as_status["retired"]) == [old.split("/")[1]], old
        assert hits(line, export) == hits(line, as_status[record["status"]]), old
