"""The single licence mapping (001 FTASKS 4.6, 4.7; FR-001.26, FR-001.27; mutation control M4).

ColBERT → ``cc-by-2.0``; Humicroedit → "not stated" with raw ``unknown`` kept; offensive-humor →
"not stated", origin ``none``; a list licence; tags only. Uploads start "not stated".
"""

from __future__ import annotations

import pytest

from src.models.source_enums import LICENCE_NOT_STATED
from src.services.sources.licence import licence_from_hub
from tests.support.hf_mock import fixture


def test_colbert() -> None:
    body = fixture("colbert_revision_short.json")
    assert licence_from_hub(body["cardData"]["license"], body["tags"]) == (
        "cc-by-2.0",
        "cc-by-2.0",
        "card_data",
    )


def test_humicroedit_unknown_is_not_stated_with_the_raw_kept() -> None:
    body = fixture("humicroedit_revision.json")
    raw, display, origin = licence_from_hub(body["cardData"].get("license"), body["tags"])
    assert (raw, display, origin) == ("unknown", LICENCE_NOT_STATED, "card_data")


@pytest.mark.parametrize("unknown", ["unknown", "Unknown", " UNKNOWN "])
def test_unknown_in_any_case_is_not_stated(unknown: str) -> None:
    assert licence_from_hub(unknown, [])[1] == LICENCE_NOT_STATED


def test_offensive_humor_has_none() -> None:
    body = fixture("offensive_revision.json")
    assert licence_from_hub((body.get("cardData") or {}).get("license"), body.get("tags")) == (
        None,
        LICENCE_NOT_STATED,
        "none",
    )


def test_a_list_licence_is_kept_raw_and_shown_joined() -> None:
    assert licence_from_hub(["mit", "apache-2.0"], []) == (
        ["mit", "apache-2.0"],
        "mit, apache-2.0",
        "card_data",
    )


def test_tags_are_the_fallback_when_the_card_says_nothing() -> None:
    assert licence_from_hub(None, ["license:cc0-1.0", "task:x"]) == ("cc0-1.0", "cc0-1.0", "tags")
    assert licence_from_hub("", ["license:unknown"])[1] == LICENCE_NOT_STATED
    assert licence_from_hub([], ["license:a", "license:b"]) == (["a", "b"], "a, b", "tags")


def test_the_card_wins_over_tags() -> None:
    assert licence_from_hub("mit", ["license:apache-2.0"])[2] == "card_data"
