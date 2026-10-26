"""The set shape of a behaviour (017 FTASKS 10.9; FR-017.50, FR-017.7).

For each fixture behaviour a set is created THROUGH MCP (``dataworks_create_detector_set`` over the
real backend): training rows, an in-distribution test, one out-of-distribution role per held-out
genre, one for real text, one per declared evaluation slice (with a ``display_name``), and
calibration negatives. ``dataworks_check_detector_set`` passes or notes with reasons: no check
refuses, D-10 states what was not measured, and every role keeps its name.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tests.support.detector_fixtures import MAPPING, ROLES, labelled_rows, version
from tests.support.mcp_live import call, live_mcp

GENRES = {
    "fixture-alpha": ("email", "chat", "report"),
    "fixture-beta": ("memo", "forum"),
}
ROLES_G = {**ROLES, "genre": "metadata"}


def rows(n: int, *, seed: int, tag: str, genres: tuple[str, ...]) -> list[dict[str, Any]]:
    out = labelled_rows(n, seed=seed, tag=tag)
    for i, r in enumerate(out):
        r["genre"] = genres[(i // 2) % len(genres)]  # crosses the label (i % 2)
    return out


def role(kind: str, version_id: str, split: str, **extra: Any) -> dict[str, Any]:
    return {
        "role": kind,
        "version_id": version_id,
        "split": split,
        "input_column": "text",
        "label_column": "label",
        "label_mapping": MAPPING,
        **extra,
    }


@pytest.mark.parametrize("key", sorted(GENRES))
async def test_a_behaviour_set_of_every_role_passes_or_notes_through_mcp(
    client: httpx.AsyncClient, operator_name: str, key: str
) -> None:
    genres = GENRES[key]
    seen, held = genres[:-1], genres[-1]
    main = version(
        {
            "train": rows(160, seed=1, tag=f"{key}-tr", genres=seen),
            "test": rows(60, seed=2, tag=f"{key}-te", genres=seen),
        },
        dataset_name=f"{key}-main",
        held_out={"test": True},
        roles=ROLES_G,
    )
    by_genre = version(
        {"test": rows(60, seed=3, tag=f"{key}-og", genres=(held,))},
        dataset_name=f"{key}-held-genre",
        roles=ROLES_G,
    )
    real = version(
        {"test": rows(60, seed=4, tag=f"{key}-rt", genres=seen)},
        dataset_name=f"{key}-real-text",
        roles=ROLES_G,
    )
    sliced = version(
        {"test": rows(60, seed=5, tag=f"{key}-sl", genres=seen)},
        dataset_name=f"{key}-slice",
        roles=ROLES_G,
    )
    negatives = version(
        {"train": [{**r, "label": "not_humorous"} for r in rows(150, seed=6, tag=f"{key}-cn",
                                                                genres=seen)]},
        dataset_name=f"{key}-negatives",
        roles=ROLES_G,
    )  # fmt: skip
    mcp, _ = live_mcp()
    created = await call(
        mcp,
        "dataworks_create_detector_set",
        {
            "name": f"{key}-shape",
            "description": "every role of a behaviour set",
            "positive_meaning": "The text shows the behaviour",
            "behaviour_key": key,
            "roles": [
                role("train", main, "train"),
                role("id_test", main, "test"),
                role("ood_eval", by_genre, "test", display_name=f"held-out genre {held}"),
                role("ood_eval", real, "test", display_name="real text"),
                role("ood_eval", sliced, "test", display_name="rule-invisible renderings"),
                role(
                    "calibration_negatives",
                    negatives,
                    "train",
                    label_mapping={"not_humorous": "negative"},
                    negatives_basis={
                        "kind": "human_labelled",
                        "label_column": "label",
                        "negative_values": ["not_humorous"],
                        "labelled_by": "the source's annotators",
                        "rule": "label = not_humorous",
                    },
                ),
            ],
            "monitored_ref": {"kind": "role", "role_index": 2},
        },
    )
    assert "id" in created, created
    names = {r["display_name"] for r in created["roles"] if r["role"] == "ood_eval"}
    assert names == {f"held-out genre {held}", "real text", "rule-invisible renderings"}
    checked = await call(
        mcp, "dataworks_check_detector_set", {"set_id": created["id"], "detail": "full"}
    )
    outcomes = {o["code"]: o for o in checked["outcomes"]}
    assert set(outcomes) == {f"D-{i}" for i in range(1, 12)}
    refused = [o for o in outcomes.values() if o["outcome"] == "refused"]
    assert refused == [], refused
    assert checked["send_allowed"] is True
    for outcome in outcomes.values():
        if outcome["outcome"] == "note":
            assert outcome["reason"], outcome  # a note says why
    notes = outcomes["D-10"]["details"]["notes"]
    assert any("Second opinion not measured" in n for n in notes)
