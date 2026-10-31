"""Siblings stay with their carrier (017 FTASKS 8.2; FR-017.34, FR-017.46).

``insert_element@1`` -> ``slot_siblings@1`` -> ``split@1.1.0`` grouped by ``pair_id`` through 003's
real executor: no ``pair_id`` spans two splits. The same rows split DELIBERATELY apart (carriers in
train, their siblings in the test split) make D-4 refuse through the real check route, naming the
crossing roles; the grouped split's set is green. Carriers are distinct random-word texts, so the
only near duplicates are a carrier and its own siblings (the check is not refused by accident).
"""

from __future__ import annotations

import json
import random
from typing import Any

import httpx
import pyarrow as pa
import pytest

from tests.fixtures import humor_pool as hp
from tests.support.curation_fixtures import run_operator
from tests.support.detector_fixtures import version
from tests.support.label_fixtures import SLOT, insert_params, pin, roles_after, vocabulary

API = "/api/v1"
ALPHA = "fixture-alpha"
WORDS = (
    "harbor lantern quarry meadow ember signal violet copper thistle granite orbit saddle "
    "willow falcon prism tundra ledger pepper cobalt marble beacon canyon walnut"
).split()
MAPPING = {"pos": "positive", "neg": "negative"}
ROLES = {"text": "content", "label": "metadata", "pair_id": "metadata"}


def carriers(n: int = 40) -> pa.Table:
    rng = random.Random(17)
    rows: list[dict[str, Any]] = []
    for i in range(n):
        words = " ".join(rng.choice(WORDS) for _ in range(14))
        rows.append(
            {
                "text": f"{i} {words} {SLOT} {' '.join(rng.choice(WORDS) for _ in range(6))}",
                "slot": SLOT,
                "kind": "valid",
                "framing": "direct",
                "form": "plain",
                "value": "4111 1111 1111 1111",
            }
        )
    return hp.with_system_columns(rows, source_id="src-pairs", content=("text",))


def with_siblings() -> pa.Table:
    table = carriers()
    roles = {c: ("content" if c == "text" else "metadata") for c in table.schema.names}
    roles = {c: r for c, r in roles.items() if not c.startswith("_dw_")}
    inserted = run_operator("insert_element", insert_params(ALPHA), table, roles)
    params = {
        "variants": json.dumps([{"kind": "empty"}, {"kind": "decoy", "ref": "lookalike"}]),
        "vocabulary": vocabulary(ALPHA),
        "element_column": "value",  # required from slot_siblings@1.1.0
        **pin(ALPHA),
    }
    siblings = run_operator("slot_siblings", params, inserted.output, roles_after(inserted))
    assert siblings.output.num_rows == 120
    return siblings.output


def plain_rows(table: pa.Table) -> list[dict[str, Any]]:
    """The rows a detector version holds: text, a label by hand (an element present is the
    positive), the pair id and the split the step chose."""
    return [
        {
            "text": r["text"],
            "label": "pos" if r["element_present"] and r["kind"] == "valid" else "neg",
            "pair_id": r["pair_id"],
            "_split": r["_dw_split"],
            "sibling": r["sibling_variant"] is not None,
        }
        for r in table.to_pylist()
    ]


def spanning(rows: list[dict[str, Any]]) -> int:
    splits: dict[str, set[str]] = {}
    for r in rows:
        splits.setdefault(r["pair_id"], set()).add(r["_split"])
    return sum(1 for s in splits.values() if len(s) > 1)


def grouped_split(table: pa.Table, group: list[str] | None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "split_names": ["train", "test"],
        "split_fractions": [0.6, 0.4],
        "held_out": [],  # siblings are generated rows: a held-out split needs the proof (D10)
        "mode": "reassign",
    }
    if group:
        params["group_columns"] = group
    roles = {c: "metadata" for c in table.schema.names if not c.startswith("_dw_")}
    roles["text"] = "content"
    return plain_rows(run_operator("split", params, table, roles).output)


def set_body(version_id: str) -> dict[str, Any]:
    def role(kind: str, split: str) -> dict[str, Any]:
        return {
            "role": kind,
            "version_id": version_id,
            "split": split,
            "input_column": "text",
            "label_column": "label",
            "label_mapping": MAPPING,
            "pair_column": "pair_id",
        }

    return {
        "name": "sibling-pairs",
        "description": "Carriers and their code-made siblings",
        "positive_meaning": "The text discloses the element",
        "roles": [role("train", "train"), role("id_test", "test")],
    }


def store(rows: list[dict[str, Any]], name: str) -> str:
    splits: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        splits.setdefault(r["_split"], []).append({k: r[k] for k in ("text", "label", "pair_id")})
    return version(splits, dataset_name=name, roles=ROLES)


async def d4_of(client: httpx.AsyncClient, version_id: str) -> dict[str, Any]:
    body = {**set_body(version_id), "name": f"pairs-{version_id[:8]}"}
    created = await client.post(f"{API}/detector-sets", json=body)
    assert created.status_code == 201, created.text
    checked = await client.post(f"{API}/detector-sets/{created.json()['id']}/checks")
    assert checked.status_code == 200, checked.text
    out: dict[str, Any] = next(o for o in checked.json()["outcomes"] if o["code"] == "D-4")
    return out


def test_a_split_grouped_by_pair_id_never_separates_siblings() -> None:
    table = with_siblings()
    grouped = grouped_split(table, ["pair_id"])
    assert spanning(grouped) == 0
    assert {r["_split"] for r in grouped} == {"train", "test"}
    # the fixture can tell the two apart: without the group, siblings DO land apart
    assert spanning(grouped_split(table, None)) > 0


async def test_siblings_split_apart_make_d4_refuse_and_grouped_ones_pass(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    table = with_siblings()
    apart = [{**r, "_split": "test" if r["sibling"] else "train"} for r in plain_rows(table)]
    refused = await d4_of(client, store(apart, "siblings-apart"))
    assert refused["outcome"] == "refused", refused
    crossing = refused["details"]["crossing"]
    (pair,) = crossing  # one pair of roles: training and the in-distribution test
    assert "training" in pair and "in-distribution test" in pair, crossing
    # every carrier's group crosses: at least one pair per carrier and sibling
    assert crossing[pair] >= 80, crossing
    green = await d4_of(client, store(grouped_split(table, ["pair_id"]), "siblings-grouped"))
    assert green["outcome"] == "green", green


@pytest.mark.parametrize(
    ("key", "genres"),
    [("fixture-alpha", ("email", "chat", "report")), ("fixture-beta", ("memo", "forum"))],
)
async def test_one_carrier_in_both_train_and_test_is_refused_by_d4(
    client: httpx.AsyncClient, operator_name: str, key: str, genres: tuple[str, ...]
) -> None:
    """FR-017.46 (FTASKS 10.4): leakage by carrier. Rows of one carrier (different texts: other
    placements, kinds or forms of the same carrier) on both sides cross through ``carrier_id``."""
    rng = random.Random(key)  # noqa: S311
    rows: list[dict[str, Any]] = []
    for i in range(80):
        words = " ".join(rng.choice(WORDS) for _ in range(16))
        rows.append(
            {
                "text": f"{key} {i} {words}",
                "label": "pos" if i % 2 else "neg",
                "pair_id": f"c{i // 2}",  # two rows per carrier: the carrier's two variants
                "_split": "train" if (i // 2) % 4 else "test",
                "genre": genres[(i // 2) % len(genres)],
            }
        )
    clean = await d4_of(client, store(rows, f"{key}-carriers-clean"))
    assert clean["outcome"] == "green", clean
    leaked = [dict(r) for r in rows]
    leaked[0]["_split"] = "train" if leaked[0]["_split"] == "test" else "test"  # one carrier split
    refused = await d4_of(client, store(leaked, f"{key}-carriers-leaked"))
    assert refused["outcome"] == "refused", refused
    (pair,) = refused["details"]["crossing"]
    assert refused["details"]["crossing"][pair] == 1
