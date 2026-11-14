"""``screen_filter@1`` (017 FTASKS 7.4, 7.5; FR-017.32): any listed flag drops the row, an empty
flag drops it too (never assumed clean), and the drop events carry the source, never the text."""

from __future__ import annotations

from collections import Counter
from typing import Any

from tests.fixtures import humor_pool as hp
from tests.support.curation_fixtures import run_operator

ROLES = {"text": "content", "a_hit": "metadata", "b_hit": "metadata"}


def _table() -> Any:
    rows = [
        {"text": "secret-one", "a_hit": True, "b_hit": False},
        {"text": "secret-two", "a_hit": False, "b_hit": True},
        {"text": "secret-three", "a_hit": None, "b_hit": False},
        {"text": "kept one", "a_hit": False, "b_hit": False},
        {"text": "kept two", "a_hit": False, "b_hit": False},
    ]
    return hp.with_system_columns(rows, source_id="src-real", content=("text",))


def test_every_listed_flag_drops_and_an_empty_flag_is_never_clean() -> None:
    reports: list[dict[str, Any]] = []
    ran = run_operator(
        "screen_filter", {"flag_columns": ["a_hit", "b_hit"]}, _table(), ROLES, report_sink=reports
    )
    assert [r["text"] for r in ran.output.to_pylist()] == ["kept one", "kept two"]
    events = [e for e in ran.events.to_pylist() if e["kind"] == "dropped"]
    assert Counter((e["reason_code"], e["statistic_name"]) for e in events) == {
        ("screened_out", "a_hit"): 1,
        ("screened_out", "b_hit"): 1,
        ("not_screened", "a_hit"): 1,
    }
    for event in events:
        assert event["statistic_text"] == "source=src-real"
        assert "secret" not in repr(event)
    (report,) = reports
    assert report["screen"] == {
        "columns": ["a_hit", "b_hit"],
        "screened_out": {"a_hit": 1, "b_hit": 1},
        "not_screened": 1,
        "dropped_by_source": {"src-real": 3},
    }
