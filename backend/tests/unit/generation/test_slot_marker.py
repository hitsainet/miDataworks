"""The slot reader and the engine's slot decision, on the garble forms of the FU-7a W4 pilots.

Pilot 1 (`gr_826fed17a5968433eb505472`, hex `4C3C18EE`): 42 discards, every one the run's hex inside
`[[`/`]]` with the word `SLOT` written as one of 16 forms; pilot 2 (`gr_b4bb8ad50c46dea1469cf144`,
hex `FAB8FD99`) added `SLOTS-`. Expected outcomes are written by hand.
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.services.generation import record_store, slot_marker
from src.services.generation.run_engine import Decision, Unit, slot_decision
from src.services.generation.steering import Check

TOKEN = "[[SLOT-4C3C18EE]]"
PILOT_1 = {  # form: how many discards pilot 1 had in it
    "SLT-": 8,
    "SL-": 7,
    "SL_OT-": 3,
    "SLOF-": 3,
    "SLAT-": 3,
    "SLOG-": 3,
    "SLOC-": 2,
    "SLSL-": 2,
    "SLIT-": 2,
    "SLOTT-": 2,
    "SL_": 2,
    "SLAD-": 1,
    "SL0T-": 1,
    "SLOP-": 1,
    "SLSLOT-": 1,
    "SLO-": 1,
}


def test_the_pilot_record_lists_42_discards() -> None:
    assert sum(PILOT_1.values()) == 42 and len(PILOT_1) == 16


@pytest.mark.parametrize("word", [*PILOT_1, "SLOTS-"])
def test_every_pilot_garble_with_the_runs_hex_is_the_slot(word: str) -> None:
    written = f"[[{word}4C3C18EE]]"
    reading = slot_marker.read(f"My SSN is {written}, thanks.", TOKEN)
    assert reading.found == 1
    assert reading.text == f"My SSN is {TOKEN}, thanks."
    assert reading.repaired_from == (written,)


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("Nothing here at all.", "slot_missing"),
        ("A form: [[SLOT-4C3C18EF]].", "slot_corrupted"),  # one hex digit changed
        ("a transcript [[slot-4c3c18ee]] in lower case", "slot_corrupted"),
        ("[[SLT-4c3c18ee]]", "slot_corrupted"),  # the hex re-cased is not the run's hex
        ("SSN: [SLOT-4C3C18EE]]", "slot_corrupted"),  # a bracket lost
        ("SSN: SLOT-4C3C18EE", "slot_corrupted"),  # no brackets
        ("[[SLT-4C3C18]]", "slot_corrupted"),  # hex shortened
        ("[[name]] wrote this", "slot_corrupted"),  # marker-shaped, not the token
    ],
)
def test_a_marker_that_is_not_the_runs_code_stays_discarded(text: str, reason: str) -> None:
    reading = slot_marker.read(text, TOKEN)
    assert reading.found == 0 and reading.text == text and reading.repaired_from == ()
    assert slot_marker.missing_reason(reading) == reason


def test_exact_tokens_are_read_as_before() -> None:
    assert slot_marker.read(f"a {TOKEN} b", TOKEN) == slot_marker.SlotReading(
        1, f"a {TOKEN} b", (), True
    )
    assert slot_marker.read(f"{TOKEN}{TOKEN}", TOKEN).found == 2
    # a garbled copy beside the token is a second slot, not a repair
    both = slot_marker.read(f"{TOKEN} and [[SLT-4C3C18EE]]", TOKEN)
    assert both.found == 2


def test_a_token_of_another_shape_is_read_exactly() -> None:
    reading = slot_marker.read("x <<MARK-1>> y [[MRK-1]]", "<<MARK-1>>")
    assert (reading.found, reading.repaired_from) == (1, ())


CHECK = Check("not_applicable")
KEPT = Decision("generated", None, CHECK)


def unit(slot: bool) -> Unit:
    return Unit(0, "k", "k", "", {"slot": "{slot}" if slot else "", "genre": "email"})


def test_the_engine_decision_repairs_and_classifies() -> None:
    decision, text, copies = slot_decision(KEPT, "x [[SL_4C3C18EE]] y", unit(True), TOKEN)
    assert (decision, text, copies) == (KEPT, f"x {TOKEN} y", ("[[SL_4C3C18EE]]",))
    decision, text, copies = slot_decision(KEPT, f"x {TOKEN} y", unit(True), TOKEN)
    assert (decision, text, copies) == (KEPT, f"x {TOKEN} y", ())
    for written, reason in (
        ("plain text", "slot_missing"),
        ("[[SLOT-4C3C18EF]]", "slot_corrupted"),
        (f"{TOKEN} [[SLT-4C3C18EE]]", "slot_repeated"),
    ):
        decision, text, copies = slot_decision(KEPT, written, unit(True), TOKEN)
        assert (decision.outcome, decision.reason_code, text, copies) == (
            "discarded",
            reason,
            written,
            (),
        ), written
    # a row with no slot: a garbled copy of the token is a slot it must not hold
    decision, _, _ = slot_decision(KEPT, "[[SLOTS-4C3C18EE]]", unit(False), TOKEN)
    assert (decision.outcome, decision.reason_code) == ("discarded", "slot_repeated")
    decision, _, _ = slot_decision(KEPT, "no marker", unit(False), TOKEN)
    assert decision == KEPT


def test_a_chunk_written_before_the_column_reads_as_it_was(tmp_path: Path) -> None:
    """A completed run's chunk files lack ``slot_repaired_from``; they read with it null."""
    old = pa.schema([f for f in record_store.CHUNK_SCHEMA if f.name != "slot_repaired_from"])
    row = dict.fromkeys(old.names)
    row.update(
        record_index=0,
        stage="respond",
        seed_position=0,
        seed_row_key="k",
        prompt_row_key="k",
        response_index=0,
        steering_check="not_applicable",
        check_reasons=[],
        outcome="generated",
        text=f"a {TOKEN}",
    )
    path = tmp_path / "respond-chunk-000000.parquet"
    pq.write_table(pa.Table.from_pylist([row], schema=old), path)
    (record,) = record_store.read_chunk(path)
    assert record.slot_repaired_from is None and record.text == f"a {TOKEN}"
