"""Mining in a second round keeps away from evaluation data (017 FTASKS 8.5; FR-017.37, FR-009.59).

``hard_negative_miner@1`` reads a probe-verdict run; when that run scored a version bound to an
evaluation role, the version the miner built is "mined from evaluation data" and D-4 refuses the
set (``services/detector_sets/mining.py``). Asserted here for a set of EACH fixture behaviour
(keyed sets with behaviour columns), through the real check route: green before the training
version's recipe mines the set's own out-of-distribution role, refused after, naming the role.
"""

from __future__ import annotations

import random
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.core.database import sync_session_factory
from src.models.label_run import LabelRun
from src.models.version import Version
from tests.support import db_factories
from tests.support.detector_fixtures import version

API = "/api/v1"
#: per fixture: (genre values, framing values) from its declared vocabulary
SHAPES = {
    "fixture-alpha": (("email", "chat", "report"), ("direct", "hedged", "quoted")),
    "fixture-beta": (("memo", "forum"), ("asks", "refuses", "describes", "deflects")),
}
MAPPING = {"pos": "positive", "neg": "negative"}
ROLES = {"text": "content", "label": "metadata", "genre": "metadata", "framing": "metadata"}


def rows(key: str, n: int, tag: str) -> list[dict[str, Any]]:
    genres, framings = SHAPES[key]
    rng = random.Random(f"{key}-{tag}")
    words = [f"{a}{b}" for a in "bcdfgklmnprstvz" for b in ("ar", "el", "in", "os", "um", "ex")]
    return [
        {
            "text": " ".join(rng.choice(words) for _ in range(12)),
            "label": "pos" if i % 2 else "neg",
            "genre": genres[i % len(genres)],
            "framing": framings[(i // 2) % len(framings)],
        }
        for i in range(n)
    ]


def role(kind: str, version_id: str, split: str) -> dict[str, Any]:
    return {
        "role": kind,
        "version_id": version_id,
        "split": split,
        "input_column": "text",
        "label_column": "label",
        "label_mapping": MAPPING,
    }


def probe_run_over(version_id: str) -> str:
    """A completed probe-verdict run over ``version_id`` (the miner's input)."""
    run_id = f"lr_{db_factories.uid()[:20]}"
    with sync_session_factory()() as db:
        db.add(
            LabelRun(
                id=run_id,
                kind="probe_verdict",
                state="completed",
                input_version_id=version_id,
                chunk_size=100,
                labeler_identity={"kind": "probe_verdict"},
                labeler_identity_hash="b" * 64,
                labeler_fingerprint="c" * 64,
                started_by="Test Operator",
                started_by_origin="operator",
            )
        )
        db.commit()
    return run_id


def mine_into(train_id: str, run_id: str) -> None:
    """Give the training version a recipe whose miner read ``run_id`` (as a build would)."""
    with sync_session_factory()() as db:
        _, rev = db_factories.recipe(
            db,
            {
                "format": "midataworks.recipe/v1",
                "steps": [
                    {
                        "operator": "hard_negative_miner",
                        "version": "1",
                        "params": {"probe_label_run_id": run_id, "mode": "near"},
                    }
                ],
            },
        )
        train = db.get(Version, train_id)
        assert train is not None
        db.execute(text("ALTER TABLE dw_versions DISABLE TRIGGER dw_versions_immutable"))
        train.recipe_hash = rev.recipe_hash
        db.commit()
        db.execute(text("ALTER TABLE dw_versions ENABLE TRIGGER dw_versions_immutable"))
        db.commit()


async def d4(client: httpx.AsyncClient, set_id: str) -> dict[str, Any]:
    checked = await client.post(f"{API}/detector-sets/{set_id}/checks")
    assert checked.status_code == 200, checked.text
    out: dict[str, Any] = next(o for o in checked.json()["outcomes"] if o["code"] == "D-4")
    return out


@pytest.mark.parametrize("key", sorted(SHAPES))
async def test_a_set_of_each_behaviour_refuses_a_role_mined_from_its_evaluation_data(
    client: httpx.AsyncClient, operator_name: str, key: str
) -> None:
    train = version(
        {"train": rows(key, 60, "tr"), "test": rows(key, 30, "te")},
        dataset_name=f"{key}-train",
        held_out={"test": True},
        roles=ROLES,
    )
    ood = version(
        {"test": rows(key, 40, "ood")},
        dataset_name=f"{key}-ood",
        held_out={"test": True},
        roles=ROLES,
    )
    created = await client.post(
        f"{API}/detector-sets",
        json={
            "name": f"{key}-mining",
            "description": "A behaviour set mined in a second round",
            "positive_meaning": "The behaviour is present",
            "behaviour_key": key,
            "roles": [
                role("train", train, "train"),
                role("id_test", train, "test"),
                role("ood_eval", ood, "test"),
            ],
        },
    )
    assert created.status_code == 201, created.text
    set_id = created.json()["id"]
    assert created.json()["behaviour_key"] == key
    assert (await d4(client, set_id))["outcome"] == "green"
    mine_into(train, probe_run_over(ood))
    refused = await d4(client, set_id)
    assert refused["outcome"] == "refused" and "mined from" in refused["reason"], refused
    # both roles on the mined version are named, each mined from the set's own OOD role
    assert refused["details"]["mined"] == {
        f"{key}-mining - training rows": f"{key}-mining - out-of-distribution evaluation",
        f"{key}-mining - in-distribution test": f"{key}-mining - out-of-distribution evaluation",
    }
