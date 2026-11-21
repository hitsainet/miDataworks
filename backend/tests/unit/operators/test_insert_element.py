"""``insert_element@1`` and the boundary finder (017 FTASKS 6.1, 6.3; FR-017.22, FR-017.24).

Through 003's real executor and the live registry, on BOTH fixture behaviours: fixture-alpha's
slot carriers (multi-byte text around the slot) and fixture-beta's words inserted into real text.
``text[ins_start:ins_end] == element_inserted`` on every row (control M3: ``ins_end - 1``).
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

import pytest

from src.operators.native.behaviour import boundaries
from tests.fixtures import humor_pool as hp
from tests.support.behaviour_fixtures import plan_document
from tests.support.curation_fixtures import run_operator
from tests.support.label_fixtures import (
    ROLES,
    SLOT,
    alpha_carriers,
    definition_text,
    insert_params,
    pin,
    rule_set_document,
    vocabulary,
)

ALPHA = "fixture-alpha"
BETA = "fixture-beta"
#: Real text with every boundary kind near its middle (line, sentence, whitespace, none).
REAL_TEXTS = [
    "First line of a forum post\nsecond line, with more words after it",
    "日本語の文です。もう一つの文です。",
    "one two three four five six",
    "unbroken",
    "Ending with a mark! Then more text follows here.",
]


def _alpha(**over: Any) -> Any:
    return run_operator("insert_element", insert_params(ALPHA, **over), alpha_carriers(), ROLES_ALL)


ROLES_ALL = {
    c: ("content" if c == "text" else "metadata")
    for c in alpha_carriers().schema.names
    if not c.startswith("_dw_")
}


def _real_text(element: str | None = "quorbish", **over: Any) -> Any:
    rows = [{"text": t, "value": element} for t in REAL_TEXTS]
    table = hp.with_system_columns(rows, source_id="src-corpus", content=("text",))
    params = {
        "mode": "real_text",
        "element_column": "value",
        "vocabulary": vocabulary(BETA),
        "definition": definition_text(BETA),
        "real_text_values": json.dumps(
            {"genre": "forum", "kind": "request", "framing": "describes", "form": "prose"}
        ),
        **pin(BETA),
        **over,
    }
    return run_operator("insert_element", params, table, {"text": "content", "value": "metadata"})


def test_the_span_holds_the_element_on_every_slot_carrier() -> None:
    ran = _alpha()
    out = ran.output.to_pylist()
    assert len(out) == 72  # every kind x framing x form x placement
    for row in out:
        assert row["element_present"] is True, row
        piece = row["text"][row["ins_start"] : row["ins_end"]]
        assert piece == row["element_inserted"], row
        assert SLOT not in row["text"]
        assert row["boundary_kind"] == "slot"
        # code points, not bytes: the multi-byte frames shift a byte offset, never this one
        assert row["ins_end"] - row["ins_start"] == len(row["element_inserted"])
    middle = [r for r in out if r["placement"] == "middle"]
    assert all(len(r["text"][: r["ins_start"]].encode()) > r["ins_start"] for r in middle)
    # every carrier is a changed row: a new key over the new text, the old one in the event
    changed = ran.events_by_reason("element_inserted")
    assert len(changed) == 72
    assert {e["new_row_key"] for e in changed} == {r["_dw_row_key"] for r in out}
    assert all(e["statistic_text"] is None for e in changed)  # no row text in an event
    assert {r["_dw_origin"] for r in out} == {"source"}


def test_real_text_takes_a_boundary_and_the_row_provenance() -> None:
    ran = _real_text()
    out = ran.output.to_pylist()
    assert len(out) == len(REAL_TEXTS)
    for row in out:
        assert (
            row["text"][row["ins_start"] : row["ins_end"]] == "quorbish" == row["element_inserted"]
        )
        assert row["boundary_kind"] in boundaries.KINDS
        assert row["placement"] in ("start", "middle", "end")
        assert row["generator"] == "real_text:src-corpus"
        assert row["behaviour_key"] == BETA and row["definition_version"] == "1"
        assert (row["genre"], row["kind"], row["framing"], row["form"]) == (
            "forum", "request", "describes", "prose",
        )  # fmt: skip
        assert row["length_band"] in ("brief", "extended")
        assert row["_dw_origin"] == "source"  # a changed row, not a new one (FR-017.7)
    events = ran.events_by_reason("element_inserted")
    assert {e["row_key"] for e in events} == {r["carrier_id"] for r in out}


@pytest.mark.parametrize(
    ("text", "placement", "expected"),
    [
        ("ab\ncd", "middle", (3, "line")),
        ("一文。二文。三文", "middle", (3, "sentence")),
        ("aa bb cc", "middle", (5, "whitespace")),
        ("unbroken", "middle", (4, "none")),
        ("some text", "start", (0, "edge")),
        ("some text", "end", (9, "edge")),
        ("x. y\nz. w", "middle", (5, "line")),  # a line break beats a nearer sentence mark
    ],
)
def test_the_boundary_finder(text: str, placement: str, expected: tuple[int, str]) -> None:
    assert boundaries.boundary(text, placement) == expected


def test_insert_at_pads_a_word_it_would_glue_to() -> None:
    assert boundaries.insert_at("abcd", 2, "X") == ("ab X cd", 3)
    assert boundaries.insert_at("ab cd", 2, "X") == ("ab X cd", 3)
    assert boundaries.insert_at("", 0, "X") == ("X", 0)


def test_a_framing_phrase_goes_before_the_element_outside_the_span() -> None:
    ran = _alpha(framing_phrase_share=1.0)
    (phrase,) = plan_document(ALPHA)["vocabulary"]["phrase_list"]
    for row in ran.output.to_pylist():
        assert row["framing_phrase"] == phrase
        assert row["text"][row["ins_start"] : row["ins_end"]] == row["element_inserted"]
        before = row["text"][: row["ins_start"]]
        assert before.endswith(phrase + " ")


def test_decoy_kinds_take_a_declared_decoy_seeded_per_row() -> None:
    """FR-017.24: lookalike rows take the rule set's decoy list; the same seed, the same values."""
    first, again = _alpha(), _alpha()
    other = run_operator(
        "insert_element", insert_params(ALPHA), alpha_carriers(), ROLES_ALL, seed=9
    )
    (decoy,) = rule_set_document(ALPHA)["decoys"]
    items = set(decoy["generator"]["list"])

    def decoys(ran: Any) -> dict[tuple[str, ...], str]:
        return {
            (r["framing"], r["form"], r["placement"]): r["element_inserted"]
            for r in ran.output.to_pylist()
            if r["decoy_id"] is not None
        }

    rows = [r for r in first.output.to_pylist() if r["kind"] == "lookalike"]
    assert len(rows) == 18 and all(r["decoy_id"] == "lookalike" for r in rows)
    plain = [r for r in rows if r["form"] == "plain"]
    assert plain and {r["element_inserted"] for r in plain} <= items
    assert all(r["element_recovered"] is True for r in rows)  # spelled decoys normalise back
    assert decoys(first) == decoys(again)
    assert decoys(other) != decoys(first)  # the step seed draws them
    assert all(r["decoy_id"] is None for r in first.output.to_pylist() if r["kind"] != "lookalike")


def test_a_real_text_decoy_for_the_second_behaviour() -> None:
    ran = _real_text(
        real_text_values=json.dumps(
            {"genre": "forum", "kind": "near_miss", "framing": "describes", "form": "prose"}
        )
    )
    (decoy,) = rule_set_document(BETA)["decoys"]
    for row in ran.output.to_pylist():
        assert row["decoy_id"] == "near_miss"
        assert row["element_inserted"] in decoy["generator"]["list"]
        assert row["text"][row["ins_start"] : row["ins_end"]] == row["element_inserted"]


def test_a_decoy_kind_without_a_pinned_rule_set_is_refused() -> None:
    from src.operators.errors import StepFailed

    with pytest.raises(StepFailed) as refused:
        run_operator(
            "insert_element", insert_params(ALPHA, pinned=False), alpha_carriers(), ROLES_ALL
        )
    assert refused.value.code == "decoy_not_declared"


def test_slot_drops_and_rows_without_text_are_counted_never_quoted() -> None:
    base = alpha_carriers().to_pylist()
    rows = []
    for i, row in enumerate(base[:6]):
        clean = {k: v for k, v in row.items() if not k.startswith("_dw_")}
        if i == 0:
            clean["text"] = "no slot here at all"
        elif i == 1:
            clean["text"] = f"{SLOT} twice {SLOT}"
        elif i == 2:
            clean["text"] = None
        rows.append(clean)
    table = hp.with_system_columns(rows, source_id="src-alpha", content=("text",))
    reports: list[dict[str, Any]] = []
    ran = run_operator(
        "insert_element", insert_params(ALPHA), table, ROLES_ALL, report_sink=reports
    )
    assert ran.output.num_rows == 3
    assert Counter(e["reason_code"] for e in ran.events.to_pylist() if e["kind"] == "dropped") == {
        "slot_missing": 1, "slot_repeated": 1, "no_carrier_text": 1,
    }  # fmt: skip
    for event in ran.events.to_pylist():
        assert event["statistic_text"] is None
        assert "no slot here" not in (event["reason"] or "")
    assert reports[0]["insert_element"]["dropped"] == {
        "no_carrier_text": 1, "slot_missing": 1, "slot_repeated": 1,
    }  # fmt: skip


def test_an_undeclared_form_is_refused_naming_it() -> None:
    from src.operators.errors import StepFailed

    rows = [
        {**{k: v for k, v in r.items() if not k.startswith("_dw_")}, "form": "shouted"}
        for r in alpha_carriers().to_pylist()[:2]
    ]
    table = hp.with_system_columns(rows, source_id="src-alpha", content=("text",))
    with pytest.raises(StepFailed) as refused:
        run_operator("insert_element", insert_params(ALPHA), table, ROLES_ALL)
    assert refused.value.code == "vocabulary_value_undeclared"
    assert refused.value.details["value"] == "shouted"


def test_the_live_registry_validates_the_parameters() -> None:
    from tests.support.curation_fixtures import registry

    reg = registry()
    ok = {"element_column": "value", "vocabulary": "@input"}
    assert reg.validate_params("insert_element", "1.0.0", ok) == []
    missing = reg.validate_params("insert_element", "1.0.0", {**ok, "mode": "real_text"})
    assert any("definition" in p for p in missing)
    bad = reg.validate_params(
        "insert_element",
        "1.0.0",
        {**ok, "vocabulary": vocabulary(BETA), "real_text_values": '{"genre": "nowhere"}'},
    )
    assert any("vocabulary_value_undeclared" in p for p in bad)


def test_roles_are_metadata_and_text_stays_content() -> None:
    ran = _alpha()
    roles = ran.meta["output_column_roles"]
    assert roles["text"] == "content"
    for name in ("ins_start", "ins_end", "element_inserted", "boundary_kind", "decoy_id"):
        assert roles[name] == "metadata"
    assert ROLES == {"text": "content"}


# --- Review R3 --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # a dot inside a number or a URL is no sentence boundary (R3 F4)
        ("Pi is 3.14159 and e is 2.71828 roughly", (19, "whitespace")),
        ("visit example.com/page.html now please ok", (27, "whitespace")),
        ("One. Two three. Four five six", (15, "sentence")),
        # never inside a user-perceived character (R3 F4)
        ("abcdéfghij", (6, "none")),  # base + combining acute stay together
        ("ab\U0001f468‍\U0001f469‍\U0001f467cd", (7, "none")),  # a ZWJ family
        ("x\U0001f1ef\U0001f1f5\U0001f1fa\U0001f1f8\U0001f1eb\U0001f1f7", (3, "none")),  # flags
        ("ab\U0001f44d\U0001f3fdcd", (4, "none")),  # a skin-tone modifier stays on its emoji
        ("a b c", (1, "whitespace")),  # two spaces equally near the middle: the EARLIER one
    ],
)
def test_the_middle_boundary_never_splits_a_number_url_or_character(
    text: str, expected: tuple[int, str]
) -> None:
    assert boundaries.boundary(text, "middle") == expected
    position = expected[0]
    new, start = boundaries.insert_at(text, position, "ELEM")
    assert new[start : start + 4] == "ELEM"
    # the real text around the element is untouched, character for character
    assert new.startswith(text[:position]) and new.endswith(text[position:])


def test_real_text_honours_the_rows_placement_and_keeps_its_provenance() -> None:
    """R3 survivors: a row's own placement column is used (not redrawn), a provenance value the
    row already holds is kept, and the length band is the band of the text WITH the element."""
    base = "a" * 395 + " tail words"
    short = "b" * 390 + " end"  # 394 chars: brief before, extended once the element is in
    rows = [
        {
            "text": short,
            "value": "quorbishxyz",
            "placement": "start",
            "genre": "memo",
            "carrier_id": "carrier-upstream",
        },
        {
            "text": base,
            "value": "quorbishxyz",
            "placement": "end",
            "genre": None,
            "carrier_id": None,
        },
    ]
    table = hp.with_system_columns(rows, source_id="src-corpus", content=("text",))
    params = {
        "mode": "real_text",
        "element_column": "value",
        "vocabulary": vocabulary(BETA),
        "definition": definition_text(BETA),
        "real_text_values": json.dumps(
            {"genre": "forum", "kind": "request", "framing": "describes", "form": "prose"}
        ),
        **pin(BETA),
    }
    roles = {
        "text": "content",
        "value": "metadata",
        "placement": "metadata",
        "genre": "metadata",
        "carrier_id": "metadata",
    }
    out = run_operator("insert_element", params, table, roles).output.to_pylist()
    first, second = out
    assert (first["placement"], first["ins_start"], first["boundary_kind"]) == ("start", 0, "edge")
    assert (second["placement"], second["boundary_kind"]) == ("end", "edge")
    assert second["ins_end"] == len(second["text"])
    assert first["genre"] == "memo" and second["genre"] == "forum"  # kept, else the given value
    assert first["carrier_id"] == "carrier-upstream"  # a value the row holds is never replaced
    assert second["carrier_id"] == table.column("_dw_row_key")[1].as_py()
    assert len(short) < 400 <= len(first["text"])
    assert first["length_band"] == "extended"  # the band of the text the probe will read


def test_real_text_mode_refuses_a_row_with_no_source() -> None:
    """R3 F6: a generated row (no source, no generator) is not real text; its generator would
    read ``real_text:None``."""
    import pyarrow as pa

    from src.operators.errors import StepFailed

    table = hp.with_system_columns(
        [{"text": "a generated passage", "value": "quorbish"}], source_id="x", content=("text",)
    )
    table = table.set_column(
        table.schema.get_field_index("_dw_source_id"),
        "_dw_source_id",
        pa.array([None], pa.string()),
    )
    params = {
        "mode": "real_text",
        "element_column": "value",
        "vocabulary": vocabulary(BETA),
        "definition": definition_text(BETA),
        "real_text_values": json.dumps(
            {"genre": "forum", "kind": "request", "framing": "describes", "form": "prose"}
        ),
    }
    with pytest.raises(StepFailed) as refused:
        run_operator("insert_element", params, table, {"text": "content", "value": "metadata"})
    assert refused.value.code == "real_text_without_source"
