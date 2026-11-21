"""A role whose input column holds chat messages (``chat_json_parser``'s ``list<struct<role,
content>>``) is checked like a text role, never a 500 (live finding 2026-11-19: every check of the
self-harm pilot's sets answered INTERNAL_ERROR once one role read a chat column).

The cause was DuckDB 2.0 refusing the arrow lambda (``m -> ...``) in the length query, the one
branch only a chat column reaches; every earlier fixture's ``text`` was a string, so no test ran it.
Lengths of a chat row are the summed lengths of its message contents.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from tests.support.detector_fixtures import (
    REPLY,
    ROLES,
    as_messages,
    humor_versions,
    labelled_rows,
    set_body,
    version,
)

API = "/api/v1"
# labelled_rows' text is "<tag> <i> " + 7 words; the id_test role reads the "te" split (60 rows).
CHECK_CODES = {f"D-{n}" for n in range(1, 12)}


async def _checked(client: httpx.AsyncClient, chat_inputs: tuple[str, ...]) -> dict[str, Any]:
    body = set_body(humor_versions(chat_inputs=chat_inputs))
    created = await client.post(f"{API}/detector-sets", json=body)
    assert created.status_code == 201, created.text
    data: dict[str, Any] = created.json()
    checks = await client.post(f"{API}/detector-sets/{data['id']}/checks")
    assert checks.status_code == 200, checks.text
    out: dict[str, Any] = checks.json()
    out["_set"] = data
    return out


def _role_id(checks: dict[str, Any], kind: str) -> str:
    return str(next(r["id"] for r in checks["_set"]["roles"] if r["role"] == kind))


async def test_a_chat_id_test_role_is_checked_with_its_summed_message_lengths(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    checks = await _checked(client, ("train",))
    assert {o["code"] for o in checks["outcomes"]} >= CHECK_CODES, checks["outcomes"]
    id_test = _role_id(checks, "id_test")
    chars = checks["profiles"][id_test]
    words = checks["profiles_words"][id_test]
    assert chars["n"] == 60
    # "te <i> " + 7 words, plus the reply: a string-cast of the list would be far longer, and
    # the first message alone would be shorter by len(REPLY).
    assert words["quantiles"]["p50"] == 2 + 7 + len(REPLY.split())
    assert words["quantiles"]["p05"] == words["quantiles"]["p99"]
    # the shortest possible row ("te 0 " + 7 three-letter words) plus the reply
    assert chars["quantiles"]["p05"] >= len("te 0 ") + 7 * 3 + len(REPLY)


async def test_every_role_reading_chat_messages_is_checked(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    checks = await _checked(client, ("train", "ood", "cal"))
    assert {o["code"] for o in checks["outcomes"]} >= CHECK_CODES, checks["outcomes"]
    for kind in ("train", "id_test", "ood_eval", "calibration_negatives"):
        assert checks["profiles"][_role_id(checks, kind)]["n"] > 0, kind


async def test_a_chat_role_beside_a_text_column_named_content_is_audited(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    """The live set's shape (2026-11-19, second finding after the deploy): train reads a plain
    column named ``content`` and id_test, from ANOTHER version, a chat ``text`` column whose
    messages also have a field ``content``. D-3's audit loads the union of the roles' columns and
    took the nested field for a top-level column ("Referenced column content not found")."""
    rows = labelled_rows(200, seed=1, tag="tr")
    train = version(
        {
            "train": [
                {**{k: v for k, v in r.items() if k != "text"}, "content": r["text"]} for r in rows
            ]
        },
        dataset_name="plain-content",
        content=("content",),
        roles={**{k: v for k, v in ROLES.items() if k != "text"}, "content": "content"},
    )
    chat = version(
        {"test": as_messages(labelled_rows(60, seed=2, tag="te"))},
        dataset_name="chat-test",
        held_out={"test": True},
    )
    others = humor_versions()
    body = set_body({**others, "train": train})
    body["roles"][0]["input_column"] = "content"
    body["roles"][1]["version_id"] = chat
    created = await client.post(f"{API}/detector-sets", json=body)
    assert created.status_code == 201, created.text
    checks = await client.post(f"{API}/detector-sets/{created.json()['id']}/checks")
    assert checks.status_code == 200, checks.text
    out = checks.json()
    assert {o["code"] for o in out["outcomes"]} >= CHECK_CODES, out["outcomes"]
