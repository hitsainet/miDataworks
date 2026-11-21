"""D4 (live, 2026-10-08): a detector set whose roles MIX paired and unpaired versions ran no check.

The leakage check took the FIRST role's pair column as one set-wide group column, so an ood_eval
role on a version with no ``pair_id`` (``mup-mental-health-balanced``) made ``POST
/detector-sets/{id}/checks`` answer 422 ``group_column_missing`` and nothing could run. Each role is
now grouped by its OWN pair column; a role that declares none contributes no groups; a role that
DECLARES a column its version lacks still refuses, naming the role.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text

from src.core.database import sync_session_factory
from tests.support.detector_fixtures import MAPPING, ROLES, labelled_rows, version

API = "/api/v1"
UNPAIRED_ROLES = {k: v for k, v in ROLES.items() if k != "pair_id"}


def unpaired(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: v for k, v in r.items() if k != "pair_id"} for r in rows]


def live_shape(*, cross_pair: bool = False) -> dict[str, str]:
    """A minimal-pair version (train + held-out test, ``pair_id``) and an unpaired OOD version."""
    train = labelled_rows(80, seed=1, tag="tr", pairs=True)
    test = labelled_rows(40, seed=2, tag="te", pairs=True)
    if cross_pair:
        test[0]["pair_id"] = train[0]["pair_id"]  # one pair group spans train and id_test
    paired = version(
        {"train": train, "test": test}, dataset_name="minimal-pairs", held_out={"test": True}
    )
    ood = version(
        {"test": unpaired(labelled_rows(60, seed=3, tag="ood"))},
        dataset_name="mup-mental-health-balanced",
        roles=UNPAIRED_ROLES,
    )
    return {"paired": paired, "ood": ood}


def body(v: dict[str, str]) -> dict[str, Any]:
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

    return {
        "name": "minimal-pair-set",
        "description": "Minimal pairs with an out-of-distribution role",
        "positive_meaning": "Most readers would find the text funny",
        "roles": [
            role("train", v["paired"], "train", pair_column="pair_id"),
            role("id_test", v["paired"], "test", pair_column="pair_id"),
            role("ood_eval", v["ood"], "test"),
        ],
    }


async def create(client: httpx.AsyncClient, v: dict[str, str]) -> dict[str, Any]:
    response = await client.post(f"{API}/detector-sets", json=body(v))
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


def d4(checks: dict[str, Any]) -> dict[str, Any]:
    return next(o for o in checks["outcomes"] if o["code"] == "D-4")


async def test_paired_and_unpaired_roles_run_their_checks(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    created = await create(client, live_shape())
    response = await client.post(f"{API}/detector-sets/{created['id']}/checks")
    assert response.status_code == 200, response.text
    outcome = d4(response.json())
    assert outcome["outcome"] == "green", outcome
    assert outcome["figure"] == "0 pairs"


async def test_pair_grouping_still_counts_a_pair_crossing_paired_roles(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    created = await create(client, live_shape(cross_pair=True))
    response = await client.post(f"{API}/detector-sets/{created['id']}/checks")
    assert response.status_code == 200, response.text
    outcome = d4(response.json())
    assert outcome["outcome"] == "refused", outcome
    # the shared group has two train rows and one id_test row: two crossing pairs
    assert sum(outcome["details"]["crossing"].values()) == 2, outcome


async def test_a_role_declaring_a_missing_pair_column_still_refuses_naming_it(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    created = await create(client, live_shape())
    ood = next(r for r in created["roles"] if r["role"] == "ood_eval")
    with sync_session_factory()() as db:  # a declaration the create-time check would refuse
        db.execute(
            text("UPDATE dw_detector_set_roles SET pair_column = 'pair_id' WHERE id = :i"),
            {"i": ood["id"]},
        )
        db.commit()
    response = await client.post(f"{API}/detector-sets/{created['id']}/checks")
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "group_column_missing"
    assert error["details"]["group_column"] == "pair_id"
    assert error["details"]["role_id"] == ood["id"]
    assert error["message"].startswith(f"Role {error['details']['role']} declares the pair column")
    assert error["details"]["role"] == "minimal-pair-set - out-of-distribution evaluation"
