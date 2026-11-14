"""ENH-006 G20: when D-10 names the re-read as its next step (``checks.definition_next``).

Each trigger alone: the catalogue's version moved with the meaning unchanged, and the meaning
changed with the version unchanged. Neither, an unverified catalogue, or no set: no next step.
"""

from __future__ import annotations

from typing import Any

from src.services.detector_sets import checks


def facts(**over: Any) -> checks.DefinitionFacts:
    base: dict[str, Any] = {
        "key": "dlp-ssn-us",
        "recorded_version": 2,
        "recorded_meaning": "m",
        "set_meaning": "m",
        "verified": True,
        "current_version": 2,
        "catalogue_meaning": "m",
        "set_id": "dts_1",
    }
    return checks.DefinitionFacts(**{**base, **over})


def test_a_moved_version_alone_names_the_reread() -> None:
    found = checks.definition_next(facts(current_version=3))
    assert found is not None
    assert found["tool"] == "dataworks_reread_behaviour_definition"
    assert found["args"] == {"set_id": "dts_1"}
    assert "v3" in found["why"]


def test_a_changed_meaning_alone_names_the_reread() -> None:
    found = checks.definition_next(facts(catalogue_meaning="another meaning"))
    assert found is not None and found["args"] == {"set_id": "dts_1"}


def test_no_next_step_when_the_set_matches_or_nothing_was_read() -> None:
    assert checks.definition_next(facts()) is None
    assert checks.definition_next(facts(current_version=3, verified=False)) is None
    assert checks.definition_next(facts(current_version=3, set_id=None)) is None
    assert checks.definition_next(None) is None
