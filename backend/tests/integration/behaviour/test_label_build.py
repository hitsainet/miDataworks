"""Labels decided by declared rules, through real builds (017 FTASKS 6.6, 6.10, 6.11; FR-017.12,
FR-017.26, FR-017.30).

The production operators, the real orchestrator (``"@input"`` and the rule-set pins resolved at
build time) and the real finalize, for BOTH fixture behaviours: carriers from two generators'
runs over the plan, then ``insert_element@1`` (fixture-alpha) -> ``rule_set@1`` ->
``derive_labels@1``. What is asserted is read from the built split files and through REST.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
import pyarrow.parquet as pq
import pytest

from src.core.database import sync_session_factory
from tests.integration.behaviour.test_carrier_modes import (
    INSTRUCTED_PROMPT,
    INSTRUCTED_SYSTEM,
    SLOT_PROMPT,
    run_body,
    started,
    template_map,
)
from tests.integration.behaviour.test_carrier_plan_build import plan_version
from tests.integration.test_version_build import request, setup
from tests.support.generation_fixtures import GEN_MODEL, Gen
from tests.support.label_fixtures import labels, rule_set_document
from tests.support.operator_build_driver import RealDriver

SLOT_TOKEN = re.compile(r"\[\[SLOT-[0-9A-F]+\]\]")
#: FR-017.12: every row of a behaviour version says where it came from and what it is.
PROVENANCE = (
    "behaviour_key",
    "definition_version",
    "genre",
    "generator",
    "carrier_id",
    "placement",
    "form",
    "framing",
    "kind",
    "length_band",
)
GEN_B = "gen-model-b"


def one_slot(messages: list[dict[str, Any]], body: dict[str, Any]) -> str:
    text = " ".join(str(m.get("content")) for m in messages)
    token = SLOT_TOKEN.search(text)
    assert token, text
    return f"Dear reader, the value {token.group(0)} is noted here."


def instructed(messages: list[dict[str, Any]], body: dict[str, Any]) -> str:
    text = " ".join(str(m.get("content")) for m in messages)
    return (
        "Here is the quorbish you asked about."
        if "complied=complied_yes" in text
        else "I will not go into that today."
    )


async def save_rule_set(client: httpx.AsyncClient, key: str) -> dict[str, Any]:
    response = await client.post("/api/v1/rule-sets", json={"document": rule_set_document(key)})
    assert response.status_code == 201, response.text
    body = response.json()
    return {
        "rule_set_name": body["name"],
        "rule_set_version": body["version"],
        "rule_set_sha256": body["sha256"],
    }


def label_steps(key: str, pin: dict[str, Any]) -> list[dict[str, Any]]:
    doc = labels(key)
    steps: list[dict[str, Any]] = []
    if key == "fixture-alpha":
        steps += [
            {
                "operator": "synth_identifier",
                "version": "1",
                "params": {
                    "scheme": "card_pan",
                    "valid_share": 1.0,
                    "prefix_set": "test",
                    "delimiters": ["space"],
                    "brands": ["visa"],
                },
            },
            {
                "operator": "insert_element",
                "version": "1.0.0",
                "params": {"element_column": "value", "vocabulary": "@input", **pin},
            },
        ]
    label_params: dict[str, Any] = {
        "label_column": doc["label_column"],
        "vocabulary": "@input",
        "truth_tables": json.dumps(doc["truth_tables"]),
        "target_checks": json.dumps(doc["target_checks"]),
        "output_prefix": doc["output_prefix"],
        **pin,
    }
    for name in ("vetoed_column", "scan_column"):
        if doc[name]:
            label_params[name] = doc[name]
    steps += [
        {
            "operator": "rule_set",
            "version": "1",
            "params": {
                **pin,
                "use": "label",
                "text_column": "text",
                "output_prefix": doc["output_prefix"],
            },
        },
        {"operator": "derive_labels", "version": "1.0.0", "params": label_params},
    ]
    return steps


async def labelled_version(
    client: httpx.AsyncClient, gen: Gen, driver: RealDriver, data_dir: Path, key: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """A plan of 60 rows, two generators' carrier runs, then the label steps over the plan."""
    plan = await plan_version(client, driver, data_dir, key, 60)
    if key == "fixture-alpha":
        mode, body, answer = "carrier_slot", {"prompt": SLOT_PROMPT}, one_slot
    else:
        mode, body = "carrier_instructed", {
            "system": INSTRUCTED_SYSTEM,
            "prompt": INSTRUCTED_PROMPT,
        }
        answer = instructed
    mapping = await template_map(client, plan, body, f"{key}-labels")
    gen.fake.gen_answer = answer
    first = await started(client, run_body(plan, mapping, mode))
    assert gen.run_until_done(first["id"]).state == "completed"
    gen.fake.resident = {**(gen.fake.resident or {}), "name": GEN_B, "revision": "b" * 40}
    second = await started(client, run_body(plan, mapping, mode, generator_model_id=GEN_B))
    assert gen.run_until_done(second["id"]).state == "completed"
    pin = await save_rule_set(client, key)
    steps = [
        {
            "operator": "dw_generated_rows",
            "version": "1",
            "params": {"generation_run_id": run["id"], "target_type": "detector"},
        }
        for run in (first, second)
    ] + label_steps(key, pin)
    ds, rev = await setup(
        client,
        {"format": "dw.recipe/v1", "steps": steps},
        name=f"{key}-labelled",
        target="detector",
    )
    response = await request(
        client,
        ds,
        rev,
        [{"kind": "version", "version_id": plan["id"]}],
        seed=1,
        bindings=[{"kind": "generation_run", "id": r["id"]} for r in (first, second)],
    )
    assert response.status_code == 202, response.text
    status = driver.run(response.json()["job_id"])
    from src.models.job import Job
    from src.models.version import VersionBuild

    with sync_session_factory()() as db:
        job = db.get(Job, response.json()["job_id"])
        assert status == "completed", job.error if job else None
        built = db.get(VersionBuild, response.json()["job_id"])
        assert built is not None and built.version_id is not None
        version_id = str(built.version_id)
    version = (await client.get(f"/api/v1/versions/{version_id}")).json()
    return plan, version


def split_rows(data_dir: Path, version: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for split in version["splits"]:
        out.extend(pq.read_table(data_dir / split["path"]).to_pylist())
    return out


@pytest.mark.parametrize("key", ["fixture-alpha", "fixture-beta"])
async def test_every_row_says_where_it_came_from_and_carries_its_facts_and_labels(
    client: httpx.AsyncClient, gen: Gen, driver: RealDriver, data_dir: Path, key: str
) -> None:
    """6.11 (FR-017.12) and 6.6 (FR-017.26), on both fixtures, through one real build."""
    plan, version = await labelled_version(client, gen, driver, data_dir, key)
    rows = split_rows(data_dir, version)
    assert rows, version
    # only carriers remain: the plan rows (no text) were dropped by insert_element (alpha); for
    # beta the label step runs over every row the runs wrote, and the plan rows hold no text
    carriers = [r for r in rows if r["text"] is not None]
    assert {r["generator"] for r in carriers} == {GEN_MODEL, GEN_B}
    assert len({r["genre"] for r in carriers}) == len(
        plan["behaviour_configuration"]["vocabulary"]["genres"]
    )
    assert {r["placement"] for r in carriers} == set(
        plan["behaviour_configuration"]["vocabulary"]["placements"]
    )
    for row in carriers:
        for column in PROVENANCE:
            assert row[column] is not None, (column, row["_dw_origin"])
        assert row["behaviour_key"] == key
    # 6.6: the rule set's fact columns are in EVERY role's split file (the empty held-out one too)
    doc = labels(key)
    facts = [doc["output_prefix"] + o["column"] for o in rule_set_document(key)["outputs"]]
    written = [doc["label_column"], "deployed_truth", "label_conflict", "label_conflict_reason"]
    for split in version["splits"]:
        names = pq.read_schema(data_dir / split["path"]).names
        assert set(facts) | set(written) <= set(names), (split["name"], names)
    # the outputs equal what the rule set states for its own cases, through the build
    if key == "fixture-alpha":
        assert all(
            r["text"][r["ins_start"] : r["ins_end"]] == r["element_inserted"] for r in carriers
        )
        for row in carriers:
            if row["kind"] == "valid" and row["form"] == "plain":
                assert row["r_fa_hit"] is True and row["label_conflict"] is False, {
                    k: row[k]
                    for k in ("text", "value", "r_fa_hit", "r_fa_spans", "label_conflict_reason")
                }
            if row["form"] == "spelled":
                assert row["r_fa_hit"] is False  # invisible to the pattern, still disclosed
        assert any(r["discloses"] for r in carriers if r["form"] == "spelled")
    else:
        assert all(r["label_conflict"] is False for r in carriers)
        assert {r["r_fb_label"] for r in carriers} == {True, False}


async def test_d3_sets_the_declared_label_sources_aside_and_reports_one_left_out(
    client: httpx.AsyncClient, gen: Gen, driver: RealDriver, data_dir: Path
) -> None:
    """6.6: every column the label reads is declared in the role's label_source_columns; D-3
    excludes each as label_source, and a column removed from the list is reported again."""
    _, version = await labelled_version(client, gen, driver, data_dir, "fixture-alpha")
    doc = labels("fixture-alpha")
    sources = [
        "kind", "framing", "element_present", "element_recovered", "deployed_truth",
        "sq_element_valid", "planned_element_valid", "decoy_id", "value", "valid",
        "perturbation", "detail",
        *(doc["output_prefix"] + o["column"] for o in rule_set_document("fixture-alpha")["outputs"]),
        doc["vetoed_column"], "label_conflict", "label_conflict_reason",
    ]  # fmt: skip

    async def d3(declared: list[str], name: str) -> dict[str, Any]:
        body = {
            "name": name,
            "behaviour_key": "fixture-alpha",
            "roles": [
                {
                    "role": "train",
                    "version_id": version["id"],
                    "split": "train",
                    "input_column": "text",
                    "label_column": doc["label_column"],
                    "label_mapping": {"true": "positive", "false": "negative"},
                    "label_source_columns": declared,
                }
            ],
        }
        created = await client.post("/api/v1/detector-sets", json=body)
        assert created.status_code == 201, created.text
        checks = await client.post(f"/api/v1/detector-sets/{created.json()['id']}/checks")
        assert checks.status_code == 200, checks.text
        return dict(next(o for o in checks.json()["outcomes"] if o["code"] == "D-3"))

    full = await d3(sources, "alpha-labels-all")
    excluded = {e["column"]: e["reason"] for e in full["details"].get("excluded", [])}
    assert {c: excluded.get(c) for c in ("kind", "framing", "r_fa_hit")} == dict.fromkeys(
        ("kind", "framing", "r_fa_hit"), "label_source"
    ), full
    left_out = await d3([c for c in sources if c != "kind"], "alpha-labels-no-kind")
    assert "kind" not in {e["column"] for e in left_out["details"].get("excluded", [])}
    named = json.dumps(left_out["details"])
    assert left_out["outcome"] == "refused" and '"kind"' in named, left_out


async def test_a_sub_question_is_its_own_set_over_the_same_versions(
    client: httpx.AsyncClient, gen: Gen, driver: RealDriver, data_dir: Path
) -> None:
    """6.10 (FR-017.30): one set per sub-question binds the same version with another label
    column and the same behaviour key; the single-role constraint is unchanged."""
    _, version = await labelled_version(client, gen, driver, data_dir, "fixture-alpha")
    doc = labels("fixture-alpha")

    def body(name: str, label_column: str, mapping: dict[str, str]) -> dict[str, Any]:
        return {
            "name": name,
            "behaviour_key": "fixture-alpha",
            "roles": [
                {
                    "role": "train",
                    "version_id": version["id"],
                    "split": "train",
                    "input_column": "text",
                    "label_column": label_column,
                    "label_mapping": mapping,
                }
            ],
        }

    main = await client.post(
        "/api/v1/detector-sets",
        json=body("alpha-main", doc["label_column"], {"true": "positive", "false": "negative"}),
    )
    sub = await client.post(
        "/api/v1/detector-sets",
        json=body(
            "alpha-element-valid",
            "sq_element_valid",
            {"valid_yes": "positive", "valid_no": "negative"},
        ),
    )
    assert main.status_code == 201 and sub.status_code == 201, (main.text, sub.text)
    a, b = main.json(), sub.json()
    assert a["behaviour_key"] == b["behaviour_key"] == "fixture-alpha"
    assert a["roles"][0]["version_id"] == b["roles"][0]["version_id"] == version["id"]
    assert (a["roles"][0]["label_column"], b["roles"][0]["label_column"]) == (
        doc["label_column"], "sq_element_valid",
    )  # fmt: skip
    # one train role per set, as before (uq_dw_detector_set_roles_single)
    twice = body("alpha-twice", doc["label_column"], {"true": "positive", "false": "negative"})
    twice["roles"].append(dict(twice["roles"][0]))
    refused = await client.post("/api/v1/detector-sets", json=twice)
    assert refused.status_code in (409, 422), refused.text
