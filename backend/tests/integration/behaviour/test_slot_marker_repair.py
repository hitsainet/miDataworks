"""A slot carrier whose token has the run's exact code but a garbled word (FU-7a W4, 2026-11-27).

The Gemma pilot `gr_826fed17a5968433eb505472` kept 58 of 100: all 42 `slot_missing` carriers held the
run's hex `4C3C18EE` inside `[[`/`]]` with the word `SLOT` corrupted, in 16 forms. Pilot 2 added
`SLOTS-` and `SLAT-`. A real carrier_slot run against the fake miServer on loopback answers each plan
row with one of those forms (or a control form) by its carrier id; the expected outcome of each form
is written here by hand, not derived from the code. Also pinned here: gap (v) (a marker that is not
the token is `slot_corrupted`, nothing at all is `slot_missing`), gap (t) (the records read renders
each record's messages, compared with what the fake server RECEIVED), gap (s) (the endpoint block
names the model the run sends) and gap (ac) (a finished run has nothing in flight).
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import update

from src.core.config import get_settings
from src.core.database import sync_session_factory
from src.models.generation import GenerationRun
from tests.integration.behaviour.test_carrier_modes import (
    CARRIER,
    RUNS,
    SLOT,
    SLOT_PROMPT,
    candidate,
    carrier_of,
    model_route,
    plan_rows,
    run_body,
    started,
    template_map,
    version_rows,
)
from tests.integration.behaviour.test_carrier_plan_build import plan_version
from tests.support.generation_fixtures import GEN_MODEL, Gen, set_role
from tests.support.mcp_live import call, live_mcp
from tests.support.operator_build_driver import RealDriver

#: The garbled words the two Gemma pilots wrote, each with the run's exact hex (the record above).
PILOT_WORDS = (
    "SLT-",
    "SL-",
    "SL_OT-",
    "SLOF-",
    "SLAT-",
    "SLOG-",
    "SLOC-",
    "SLSL-",
    "SLIT-",
    "SLOTT-",
    "SL_",
    "SLAD-",
    "SL0T-",
    "SLOP-",
    "SLSLOT-",
    "SLO-",
    "SLOTS-",
)


def forms(token: str) -> list[tuple[str, str, str | None]]:
    """``(name, the marker as written, expected outcome or reason)``; None = kept as written."""
    hex_ = token[len("[[SLOT-") : -2]
    wrong = hex_[:-1] + ("0" if hex_[-1] != "0" else "1")
    out: list[tuple[str, str, str | None]] = [
        (f"garbled {w}", f"[[{w}{hex_}]]", "repaired") for w in PILOT_WORDS
    ]
    out += [
        ("exact", token, None),
        ("wrong hex", f"[[SLOT-{wrong}]]", "slot_corrupted"),
        ("lower case", token.lower(), "slot_corrupted"),
        ("one bracket", f"[SLOT-{hex_}]]", "slot_corrupted"),
        ("absent", "", "slot_missing"),
        ("exact and a garbled copy", f"{token} then [[SLT-{hex_}]]", "slot_repeated"),
        ("two garbled copies", f"[[SLT-{hex_}]] and [[SL-{hex_}]]", "slot_repeated"),
    ]
    return out


def form_of(carrier_id: str, token: str) -> tuple[str, str, str | None]:
    table = forms(token)
    return table[int(carrier_id, 16) % len(table)]


def answer(messages: list[dict[str, Any]], body: dict[str, Any]) -> str:
    text = " ".join(str(m.get("content")) for m in messages)
    token, carrier = SLOT.search(text), CARRIER.search(text)
    assert token and carrier, text
    _, marker, _ = form_of(carrier.group(1), token.group(0))
    return f"Before {marker} after." if marker else "A carrier with nothing in it."


@pytest.fixture
def small_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "generation_chunk_size", 5)


async def test_a_garbled_word_with_the_runs_code_is_the_slot_and_is_counted(
    client: httpx.AsyncClient, gen: Gen, driver: RealDriver, data_dir: Path, small_chunks: None
) -> None:
    plan = await plan_version(client, driver, data_dir, "fixture-alpha", 160)
    rows = model_route(plan_rows(data_dir, plan))
    mapping = await template_map(client, plan, {"prompt": SLOT_PROMPT}, "alpha-repair")
    set_role("generation", gen.base_url, "role-model-not-the-generator")
    gen.fake.gen_answer = answer

    live: list[dict[str, Any]] = []

    def watch(_: dict[str, Any]) -> None:
        # the 6th request is sent after the first chunk (5 units) committed: the live counts
        if gen.fake.chat_calls == 6:
            with sync_session_factory()() as db:
                live.append(dict(db.get(GenerationRun, run_id).counts))  # type: ignore[union-attr]

    gen.fake.on_chat = watch
    planned = await client.post(f"{RUNS}/plan", json=run_body(plan, mapping, "carrier_slot"))
    assert planned.status_code == 200, planned.text
    endpoint = planned.json()["generation_endpoint"]
    assert endpoint["model_id"] == "role-model-not-the-generator"
    assert endpoint["sends_model_id"] == GEN_MODEL and "sends its own" in endpoint["model_id_note"]
    run = await started(client, run_body(plan, mapping, "carrier_slot"))
    run_id, token = run["id"], run["slot_token"]
    assert run["generation_endpoint"]["sends_model_id"] == GEN_MODEL
    assert (run["requests_in_flight"], run["requests_in_flight_now"]) == (1, 0), "queued"
    done = gen.run_until_done(run_id)
    assert done.state == "completed", done.error

    chats = gen.chats()
    assert len(chats) == len(rows)
    by_carrier = {carrier_of(c): c for c in chats}
    expect = {cid: form_of(cid, token) for cid in by_carrier}
    names = Counter(name for name, _, _ in expect.values())
    assert all(names[f"garbled {w}"] for w in PILOT_WORDS), "every pilot form must be exercised"
    outcome = Counter(e or "kept" for _, _, e in expect.values())
    assert done.counts["slot_garbled_repaired"] == outcome["repaired"]
    assert done.counts["respond:generated"] == outcome["kept"] + outcome["repaired"]
    for reason in ("slot_corrupted", "slot_missing", "slot_repeated"):
        assert done.counts[f"reason:{reason}"] == outcome[reason], reason
    first = [expect[carrier_of(c)][2] for c in chats[:5]]
    assert live and live[0].get("slot_garbled_repaired", 0) == first.count("repaired"), live

    mcp, _ = live_mcp()
    items: list[dict[str, Any]] = []
    for page in (1, 2, 3):
        read = await call(
            mcp,
            "dataworks_get_generation_records",
            {"run_id": run_id, "include_messages": True, "page": page, "limit": 50},
        )
        items += read["items"]
        assert read["messages_note"] is None
    assert len(items) == len(rows)
    for item in items:
        cid = CARRIER.search(item["messages"][-1]["content"]).group(1)  # type: ignore[union-attr]
        name, marker, want = expect[cid]
        # gap (t): what the record says was sent is what the server received
        assert item["messages"] == by_carrier[cid].body["messages"], name
        assert item["prompt"] == "", "a carrier record still stores no prompt"
        if want == "repaired":
            assert item["outcome"] == "generated" and item["reason_code"] is None, name
            assert item["text"] == f"Before {token} after.", name
            assert item["slot_repaired_from"] == [marker], name
        elif want is None:
            assert item["outcome"] == "generated" and item["slot_repaired_from"] is None, name
            assert item["text"] == f"Before {token} after."
        else:
            assert (item["outcome"], item["reason_code"]) == ("discarded", want), name
            assert item["slot_repaired_from"] is None, name
    plain = await call(mcp, "dataworks_get_generation_records", {"run_id": run_id, "limit": 5})
    assert all(i["messages"] is None for i in plain["items"])

    read = (await client.get(f"{RUNS}/{run_id}")).json()
    assert (read["requests_in_flight"], read["requests_in_flight_now"]) == (1, 0), "gap (ac)"
    with sync_session_factory()() as db:
        db.execute(update(GenerationRun).where(GenerationRun.id == run_id).values(state="running"))
        db.commit()
    assert (await client.get(f"{RUNS}/{run_id}")).json()["requests_in_flight_now"] == 1
    with sync_session_factory()() as db:
        db.execute(
            update(GenerationRun).where(GenerationRun.id == run_id).values(state="completed")
        )
        db.commit()

    # the candidate version carries each repaired carrier with the canonical token, once
    version = await candidate(client, driver, run_id)
    generated = [r for r in version_rows(data_dir, version) if r["_dw_origin"] == "generated"]
    assert len(generated) == outcome["kept"] + outcome["repaired"]
    assert all(r["text"] == f"Before {token} after." for r in generated)
