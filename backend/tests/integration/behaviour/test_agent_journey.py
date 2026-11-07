"""The whole pipeline through MCP, for two different behaviours (017 FTASKS 13.1 - 13.5; FR-017.60).

An agent drives a behaviour detector dataset from an empty key to a sent pair of detector sets
using MCP tool calls ONLY, through the LIVE category registry over the real backend
(``tests/support/mcp_live.py``: the real ``DataworksClient`` over the FastAPI app, the agent header
on every call). It runs the SAME code for ``fixture-alpha`` (slot carriers for an insertable
element, a validator rule set) and ``fixture-beta`` (instructed carriers, no element): every
difference between the two runs is behaviour data read from ``tests/fixtures/behaviours/<key>/``,
so a pipeline coupled to one behaviour turns one of the two red.

Steps (FPRD §2.4): status -> resolve (miStudio's catalogue, the fake) -> rule set -> generation
templates -> carrier plan recipe -> index source -> plan build -> one carrier run per generator ->
the labelled build (the runs' carriers, the rule-set labels, a value-directed held-out split by
genre with its proof) -> stratified selection -> second opinion on the paid classifier (waits for
the operator's ``paid_classifier_run``) -> agreement by stratum -> disagreements to review -> main
and sub-question sets -> check -> one send for both (waits for the operator's ``hub_push``) ->
status complete.

What is not the agent (FTID 017 X-55): the operator, through REST, approves and stores the Hub
token (Settings); the WORKERS (build executor, generation, labeling, publish and send job bodies)
run in-process when the agent has started a job, exactly as ``tests/support/*`` drive them for the
slice tests; the second generator is made resident in the fake miLLM as the operator would load it.
Only the network to the fakes (miLLM, TypeSafe, the Hub, miStudio) is replaced. Each step's payload
and the call count per tool are asserted (13.2 - 13.4); the test records every REST call it makes
itself and allows only the operator's (13.4).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pyarrow.parquet as pq
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.core.database import sync_session_factory
from src.models import DetectorSend
from tests.integration.behaviour.test_carrier_modes import (
    INSTRUCTED_PROMPT,
    INSTRUCTED_SYSTEM,
    SLOT_PROMPT,
)
from tests.integration.behaviour.test_label_build import (
    GEN_B,
    instructed,
    label_steps,
    one_slot,
)
from tests.support.behaviour_fixtures import BEHAVIOURS, FIXTURES, plan_document
from tests.support.fake_mistudio import catalogue_head
from tests.support.generation_fixtures import GEN_MODEL, Gen
from tests.support.label_fixtures import labels, rule_set_document
from tests.support.mcp_live import call, live_mcp
from tests.support.operator_build_driver import RealDriver
from tests.support.publish_fixtures import (
    publisher,  # noqa: F401 - fixture
    store_token,
)
from tests.support.second_opinion import TEMPLATE, Paid, paid
from tests.support.send_fixtures import NAMESPACE, SendDriver, sender  # noqa: F401 - fixture

__all__ = ["paid", "publisher", "sender"]

PLAN_ROWS = 160
CARRIER = re.compile(r"Carrier ([0-9a-f]+)")
LETTERS = str.maketrans("0123456789", "ghijklmnop")
REGISTER = "/api/v1/probe-monitors/datasets"
#: The REST calls the test itself may make: the operator's, never the agent's (13.4).
OPERATOR_CALLS = re.compile(
    r"^(POST /api/v1/approvals/[^/]+/approve|PUT /api/v1/settings/hf_token)$"
)


def behaviour_data(key: str) -> dict[str, Any]:
    """Everything that differs between two behaviours, read from the behaviour's own files."""
    doc = labels(key)
    second = json.loads((FIXTURES / key / "second_opinion.json").read_text(encoding="utf-8"))
    plan = plan_document(key)
    sq_name, sq_table = next(iter(doc["truth_tables"]["sub_questions"].items()))
    sq_values = list(dict.fromkeys(r["value"] for r in sq_table["rows"]))
    # a behaviour whose carriers are written whole has no slot to fill: instructed carriers
    whole = plan["vocabulary"]["placements"] == ["whole"]
    carrier_mode = "carrier_instructed" if whole else "carrier_slot"
    return {
        "doc": doc,
        "plan": plan,
        "mode": carrier_mode,
        "template": (
            {"prompt": SLOT_PROMPT}
            if carrier_mode == "carrier_slot"
            else {"system": INSTRUCTED_SYSTEM, "prompt": INSTRUCTED_PROMPT}
        ),
        "answer": one_slot if carrier_mode == "carrier_slot" else instructed,
        "question": second["questions"][doc["label_column"]],
        "sub_question": {
            "column": f"sq_{sq_name}",
            "mapping": {v: ("positive" if i == 0 else "negative") for i, v in enumerate(sq_values)},
        },
        "held_out_genre": plan["vocabulary"]["genres"][-1],
    }


#: The two carrier runs' slot tokens, pinned (one per ``start_generation``, in order). A slot run's
#: token is random per run (TD-017.11) and sits in every carrier's text, so it feeds the carriers'
#: row keys and through them every seeded per-row draw (the synthesised values, the decoys) and the
#: split: unpinned, the seeds below did not make the labelled version reproducible, and D-3/D-4
#: refused on one run in three (CI 41197720277, 2026-10-31).
SLOT_TOKENS = ("[[SLOT-5EED0001]]", "[[SLOT-5EED0002]]")


@pytest.fixture
def pinned_slot_tokens(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every slot token the runs are given, in order; the test asserts each run recorded its own."""
    from src.services.generation import run_service

    tokens = iter(SLOT_TOKENS)
    given: list[str] = []

    def pinned() -> str:
        given.append(next(tokens))
        return given[-1]

    monkeypatch.setattr(run_service, "new_slot_token", pinned)
    return given


def distinct(answer: Any) -> Any:
    """The behaviour's fake answer, made unique per carrier and generator (letters only, so no
    rule signal can fire on it) and of one length: two carriers never share a text, as real
    generations do not. The reference is several words, not one: the near-duplicate check (D-4)
    reads word 5-grams, and two carriers holding the same spelled decoy ("four one one one one
    ...", 5-grams that repeat) were 0.846 alike with a one-word reference, over its 0.8."""

    def wrapped(messages: list[dict[str, Any]], body: dict[str, Any]) -> str:
        found = CARRIER.search(" ".join(str(m.get("content")) for m in messages))
        assert found is not None
        tag = f"{found.group(1)}{body['model']}".lower().translate(LETTERS)
        # every answer padded to one length: the fake's answers must not differ in length by
        # label (the shortcut audit D-3 rightly refuses a set whose text length predicts it)
        base = answer(messages, body).ljust(48, ".")
        letters = re.sub(r"[^a-z]", "", tag)
        words = " ".join(letters[i : i + 6] for i in range(0, len(letters), 6))
        return f"{base} Ref {words}."

    return wrapped


def test_two_carriers_with_one_spelled_decoy_are_not_near_duplicates() -> None:
    """The flake of 2026-10-31 (CI 41197720277): two carriers given the same lookalike decoy in
    the spelled form were near-duplicates under the leakage check's own measure, so D-4 refused
    whenever the split put them in different roles. The fake's reference must keep them apart:
    every decoy of fixture-alpha's rule set, rendered spelled by the build's renderer, between two
    carriers of each generator, stays below the check's threshold."""
    from src.rules import renderings
    from src.services.curation import leakage_service, minhash

    document = rule_set_document("fixture-alpha")
    [spelled] = [r for r in document["renderings"] if r["rendering"] == "spelled"]
    decoys = [v for d in document["decoys"] for v in d["generator"]["list"]]
    answer = distinct(one_slot)
    token = SLOT_TOKENS[0]

    def text(carrier: str, model: str, element: str) -> str:
        prompt = SLOT_PROMPT.format(
            carrier_id=carrier,
            length_band="long",
            genre="email",
            framing="direct",
            slot=token,
            placement="middle",
        )
        made = answer([{"role": "user", "content": prompt}], {"model": model})
        assert made.count(token) == 1
        return made.replace(token, renderings.render(spelled, element))

    def alike(a: str, b: str) -> float:
        size, kind = leakage_service.DEFAULT_SIZE, leakage_service.DEFAULT_SHINGLE
        return minhash.exact_jaccard(
            {int(h) for h in minhash.shingles(a, kind, size)},
            {int(h) for h in minhash.shingles(b, kind, size)},
        )

    first, second = "0177b1f1404e8bd30243b04e", "029620747f1cee6c0d646a1c"
    worst = max(
        alike(text(first, model, decoy), text(second, model, decoy))
        for decoy in decoys
        for model in (GEN_MODEL, GEN_B)
    )
    assert worst < leakage_service.DEFAULT_THRESHOLD, worst


def firing_text(document: dict[str, Any]) -> str:
    """A text the behaviour's own rule set says fires (its first firing case): real text that a
    screen must drop. Taken from the rule set's data, never written here."""
    flags = [o["column"] for o in document["outputs"] if o["type"] == "bool"]
    for case in document["cases"]:
        if any(case["expect"]["outputs"].get(f) is True for f in flags):
            given = case["input"]
            return str(given.get("text") or f"{given['before']}{given['value']}{given['after']}")
    raise AssertionError("the rule set declares no firing case")


def real_text(document: dict[str, Any], clean: int = 40) -> bytes:
    rows = [{"text": firing_text(document), "label": "unlabelled"}]
    rows += [
        {
            "text": f"A plain line about the weather, item {chr(97 + i % 26)}{i}.",
            "label": "unlabelled",
        }
        for i in range(clean)
    ]
    return "".join(json.dumps(r) + "\n" for r in rows).encode()


class Agent:
    """MCP calls only, counted per tool; a tool missing from the registry fails at its step."""

    def __init__(self) -> None:
        self.mcp, self.client = live_mcp()
        self.calls: Counter[str] = Counter()

    async def __call__(self, tool: str, **args: Any) -> Any:
        self.calls[tool] += 1
        return await call(self.mcp, tool, args)


class Operator:
    """The operator's REST calls, recorded (13.4: the only REST the test makes)."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.calls: list[str] = []
        client.event_hooks["request"].append(self._record)

    async def _record(self, request: httpx.Request) -> None:
        self.calls.append(f"{request.method} {request.url.path}")

    async def approve(self, approval_id: str) -> None:
        got = await self.client.post(f"/api/v1/approvals/{approval_id}/approve")
        assert got.status_code == 200, got.text


@contextmanager
def real_network() -> Iterator[None]:
    """Generation reaches the fake miLLM on loopback over real HTTP (``gen``); the labeling
    fixtures route the endpoint caller to their in-memory fakes. Each phase gets its own."""
    from src.clients import endpoint_caller

    previous = endpoint_caller.install_transport(None)
    try:
        yield
    finally:
        endpoint_caller.install_transport(previous)


def run_imports(driver: RealDriver) -> list[dict[str, Any]]:
    """The worker for ``import_source`` jobs the agent started (the curation queue)."""
    from src.workers import source_tasks

    pending = [a for n, a in driver.sent if n == "midataworks.sources.import_source"]
    driver.sent = [s for s in driver.sent if s[0] != "midataworks.sources.import_source"]
    return [source_tasks.import_source(*args) for args in pending]


async def build(agent: Agent, driver: RealDriver, **args: Any) -> dict[str, Any]:
    """The agent starts a build; the build worker runs it; the agent reads the version."""
    started = await agent("dataworks_build_version", **args)
    assert driver.run(started["job_id"]) == "completed"
    job = await agent("dataworks_job_status", job_id=started["job_id"])
    assert job["status"] == "completed", job
    version: dict[str, Any] = await agent(
        "dataworks_get_version", version_id=job["result"]["version_id"]
    )
    return version


def label_sources(data_dir: Path, version: dict[str, Any], text: str) -> list[str]:
    """Every column of the version but the text and the system columns: the facts and labels the
    rules wrote, declared as the label's sources (D-3) without naming any behaviour's columns."""
    train = next(s for s in version["splits"] if s["name"] == "train")
    names = pq.read_schema(data_dir / train["path"]).names
    return [n for n in names if n != text and not n.startswith("_dw_")]


def values_of(data_dir: Path, version: dict[str, Any], split: str, column: str) -> dict[str, int]:
    entry = next(s for s in version["splits"] if s["name"] == split)
    table = pq.read_table(data_dir / entry["path"], columns=[column])
    return dict(
        Counter(
            str(v).lower() if isinstance(v, bool) else str(v) for v in table.column(0).to_pylist()
        )
    )


@pytest.mark.parametrize("key", BEHAVIOURS)
async def test_the_whole_pipeline_through_mcp(
    client: httpx.AsyncClient,
    driver: RealDriver,
    gen: Gen,
    paid: Paid,  # noqa: F811 - the fixture
    sender: SendDriver,  # noqa: F811 - the fixture
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    pinned_slot_tokens: list[str],
    key: str,
) -> None:
    from src.core.celery_app import celery_app

    # one router for every Celery send, so each worker below finds its jobs (the fixtures each
    # patch ``send_task``; the last one would otherwise swallow the others' dispatches)
    def send(name: str, args: list[Any] | None = None, **kwargs: Any) -> Any:
        if name.startswith("midataworks.publish."):
            return sender.publisher.send_task(name, args, **kwargs)
        return driver.send_task(name, args, **kwargs)

    monkeypatch.setattr(celery_app, "send_task", send)
    data = behaviour_data(key)
    doc = data["doc"]
    operator = Operator(client)
    await store_token(client)  # the operator's Hub token (Settings), before any send
    sender.mistudio.serves_behaviour = True
    sender.mistudio.behaviours[key] = catalogue_head(key, positive_meaning=f"{key} is present")
    agent = Agent()
    try:
        # -- status, then resolve (FR-017.56, FR-017.10)
        first = await agent("dataworks_get_behaviour_status", behaviour_key=key)
        assert first["next"]["tool"] == "dataworks_resolve_behaviour" and not first["complete"]
        resolved = await agent("dataworks_resolve_behaviour", behaviour_key=key)
        assert resolved["verified"] is True and resolved["record"]["key"] == key
        record = resolved["record"]

        # -- the rule set (016) and the templates, under the key
        saved = await agent("dataworks_save_rule_set", document=rule_set_document(key))
        pin = {
            "rule_set_name": saved["name"],
            "rule_set_version": saved["version"],
            "rule_set_sha256": saved["sha256"],
        }
        genres = data["plan"]["vocabulary"]["genres"]
        templates = {}
        for genre in genres:
            t = await agent(
                "dataworks_save_generation_template",
                name=f"{key}-carrier-{genre}",
                kind="respond",
                body=data["template"],
                behaviour_key=key,
            )
            assert t["behaviour_key"] == key
            templates[genre] = t["id"]

        # -- the carrier plan: recipe, index source, build (FR-017.13, FR-017.55)
        plan_params = {
            "definition": json.dumps(record),
            "vocabulary": json.dumps(data["plan"]["vocabulary"]),
            "shares": json.dumps(data["plan"]["shares"]),
            "rows": PLAN_ROWS,
            "templated_share": data["plan"]["templated_share"],
            "templated_ceiling": data["plan"]["templated_ceiling"],
        }
        plan_recipe = await agent(
            "dataworks_save_recipe",
            name=f"{key}-plan",
            body={
                "format": "dw.recipe/v1",
                "steps": [{"operator": "carrier_plan", "version": "1.0.0", "params": plan_params}],
            },
            behaviour_key=key,
        )
        index = await agent("dataworks_create_index_source", rows=PLAN_ROWS)
        [imported] = run_imports(driver)
        assert (await agent("dataworks_job_status", job_id=index["job_id"]))[
            "status"
        ] == "completed"
        dataset = await agent(
            "dataworks_create_dataset", name=f"{key}-journey", target_type="detector"
        )
        plan = await build(
            agent,
            driver,
            dataset_id=dataset["id"],
            recipe_revision_id=plan_recipe["revision_id"],
            inputs=[{"kind": "source", "source_id": imported["source_id"]}],
            seed=7,
            column_roles={"idx": "content"},
        )
        assert plan["behaviour_definition"]["key"] == key
        assert plan["behaviour_configuration"]["rows"] == PLAN_ROWS  # recorded as supplied (11.3)
        recorded = plan["behaviour_configuration"]["vocabulary"]
        supplied = data["plan"]["vocabulary"]
        # every supplied value is recorded as supplied; what was left out reads its stated default
        assert {k: recorded[k] for k in supplied} == supplied
        assert {k: v for k, v in recorded.items() if k not in supplied} in ({}, {"phrase_list": []})

        # -- one carrier run per generator (FR-017.14, .15, .18)
        gen.fake.gen_answer = distinct(data["answer"])
        runs = []
        for model in (GEN_MODEL, GEN_B):
            if model == GEN_B:  # the operator loads the second generator in miLLM
                gen.fake.resident = {
                    **(gen.fake.resident or {}),
                    "name": GEN_B,
                    "revision": "b" * 40,
                }
            with real_network():  # the start reads the generation endpoint as the run will
                run = await agent(
                    "dataworks_start_generation",
                    mode=data["mode"],
                    input_version_id=plan["id"],
                    prompt_column="text",
                    seed_splits=["train"],
                    target_type="detector",
                    generator_model_id=model,
                    template_map={"column": "genre", "templates": templates},
                    behaviour_key=key,
                    seed=5,
                )
            with real_network():
                assert gen.run_until_done(run["id"]).state == "completed"
            got = await agent("dataworks_get_generation_run", run_id=run["id"])
            assert got["state"] == "completed" and got["behaviour_key"] == key
            # the run recorded the pinned token (a slot run; an instructed run takes none)
            slot = data["mode"] == "carrier_slot"
            assert pinned_slot_tokens == list(SLOT_TOKENS[: len(runs) + 1] if slot else ())
            assert got["slot_token"] == (SLOT_TOKENS[len(runs)] if slot else None), got
            runs.append(got)
        assert {c.body["model"] for c in gen.chats()} == {GEN_MODEL, GEN_B}

        # -- the labelled build: carriers, labels from the rule set, a value-directed split
        held = data["held_out_genre"]
        split = {
            "operator": "split",
            "version": "1.1.0",
            "params": {
                "split_names": ["train", "test", "ood_genre"],
                "split_fractions": [0.75, 0.25, 0.0],
                "held_out": ["ood_genre"],
                "group_columns": ["carrier_id"],
                "directing_columns": ["genre"],
                "held_out_values": [held],
                "held_out_value_columns": ["genre"],
                "held_out_split_names": ["ood_genre"],
            },
        }
        steps = [
            {
                "operator": "dw_generated_rows",
                "version": "1",
                "params": {"generation_run_id": r["id"], "target_type": "detector"},
            }
            for r in runs
        ]
        steps.append(
            {
                "operator": "metadata_value_filter",
                "version": "1.0.0",
                "params": {"column": "text", "values": ["(empty)"]},
            }
        )
        steps += label_steps(key, pin)
        steps.append(split)
        label_recipe = await agent(
            "dataworks_save_recipe",
            name=f"{key}-labelled",
            body={"format": "dw.recipe/v1", "steps": steps},
            behaviour_key=key,
        )
        labelled = await build(
            agent,
            driver,
            dataset_id=dataset["id"],
            recipe_revision_id=label_recipe["revision_id"],
            inputs=[{"kind": "version", "version_id": plan["id"]}],
            seed=1,
            bindings=[{"kind": "generation_run", "id": r["id"]} for r in runs],
        )
        by_split = {s["name"]: s for s in labelled["splits"]}
        proof = by_split["ood_genre"]["proof"]
        assert proof["passes"] and proof["held_out_values"] == {"genre": [held]}
        assert by_split["ood_genre"]["rows"] > 0 and by_split["train"]["rows"] > 0
        assert set(values_of(data_dir, labelled, "train", "genre")) == set(genres) - {held}
        assert set(values_of(data_dir, labelled, "ood_genre", "behaviour_key")) == {key}

        # -- stratified selection for the second opinion (FR-017.38)
        select = await agent(
            "dataworks_save_recipe",
            name=f"{key}-selection",
            body={
                "format": "dw.recipe/v1",
                "steps": [
                    {
                        "operator": "sample",
                        "version": "1.1.0",
                        "params": {
                            "mode": "stratified",
                            "strata": ["genre", "generator"],
                            "per_stratum": 6,
                            "always_keep_column": "label_conflict",
                        },
                    }
                ],
            },
            behaviour_key=key,
        )
        selection = await build(
            agent,
            driver,
            dataset_id=dataset["id"],
            recipe_revision_id=select["revision_id"],
            inputs=[{"kind": "version", "version_id": labelled["id"]}],
            seed=3,
        )
        assert 0 < selection["total_rows"] <= labelled["total_rows"]

        # -- the second opinion on the paid classifier: the operator approves (FR-017.40)
        template = await agent(
            "dataworks_save_template", name=TEMPLATE["name"], body=TEMPLATE["body"]
        )
        handoff = f"{key}-ho"
        pending = await agent(
            "dataworks_start_label_run",
            input_version_id=selection["id"],
            role="classifier",
            template_id=template["id"],
            question=data["question"],
            field_map={"text": "text"},
            threshold_positive=0.5,
            threshold_negative=0.2,
            positive_label="yes",
            negative_label="no",
            target_column=doc["label_column"],
            handoff_id=handoff,
            behaviour_key=key,
        )
        assert pending["status"] == "pending" and pending["action"] == "paid_classifier_run"
        await operator.approve(pending["approval_id"])
        approval = await agent("dataworks_get_approval_status", approval_id=pending["approval_id"])
        assert approval["status"] in ("approved", "executed"), approval
        # the approval names what it made (a pre-existing gap fixed here: it named nothing)
        assert approval["result_kind"] == "lr", approval
        run_id = approval["result_id"]
        listed = await agent("dataworks_list_label_runs", behaviour_key=key)
        assert [r["id"] for r in listed["items"]] == [run_id]
        assert paid.lab.run_until_done(run_id).state == "completed"
        scored = paid.scoring()
        assert len(scored) == selection["total_rows"], "one paid call per selected row"
        label_run = await agent("dataworks_get_label_run", run_id=run_id)
        assert label_run["behaviour_key"] == key and label_run["handoff_id"] == handoff

        # -- agreement by stratum, then the disagreements to review (FR-017.43, FR-017.44)
        agreement = await agent(
            "dataworks_get_label_run_agreement",
            run_id=run_id,
            column=doc["label_column"],
            positive_value="true",
            by=["genre", "generator"],
        )
        assert agreement["applicable"] is True
        assert agreement["strata"]["cells_total"] >= len(genres)
        review = await agent(
            "dataworks_review_disagreements",
            handoff_id=handoff,
            column=doc["label_column"],
            by=["genre"],
        )
        assert review["disagreements"] > 0 and review["queues"] >= 1

        # -- real text, screened by the behaviour's rule set: the calibration negatives
        #    (FR-017.32, FR-017.33)
        import base64

        document = rule_set_document(key)
        uploaded = await agent(
            "dataworks_upload_file",
            filename=f"{key}-real-text.jsonl",
            content_base64=base64.b64encode(real_text(document)).decode(),
            split="train",
        )
        [real] = run_imports(driver)
        assert (await agent("dataworks_job_status", job_id=uploaded["job_id"]))[
            "status"
        ] == "completed"
        flags = ["s_" + o["column"] for o in document["outputs"] if o["type"] == "bool"]
        screen_recipe = await agent(
            "dataworks_save_recipe",
            name=f"{key}-negatives",
            body={
                "format": "dw.recipe/v1",
                "steps": [
                    {
                        "operator": "rule_set",
                        "version": "1",
                        "params": {
                            **pin,
                            "use": "screen",
                            "text_column": "text",
                            "output_prefix": "s_",
                        },
                    },
                    {
                        "operator": "screen_filter",
                        "version": "1.0.0",
                        "params": {"flag_columns": flags},
                    },
                ],
            },
            behaviour_key=key,
        )
        negatives = await build(
            agent,
            driver,
            dataset_id=dataset["id"],
            recipe_revision_id=screen_recipe["revision_id"],
            inputs=[{"kind": "source", "source_id": real["source_id"]}],
            seed=2,
            column_roles={"text": "content", "label": "metadata"},
        )
        assert negatives["total_rows"] == 40, "the firing real-text row was screened out"

        # -- the main set and one sub-question set over the same versions (FR-017.30, .50)
        sources = label_sources(data_dir, labelled, "text")

        def roles(column: str, mapping: dict[str, str]) -> list[dict[str, Any]]:
            return [
                {
                    "role": role,
                    "version_id": labelled["id"],
                    "split": split_name,
                    "input_column": "text",
                    "label_column": column,
                    "label_mapping": mapping,
                    "label_source_columns": [c for c in sources if c != column],
                }
                for role, split_name in (
                    ("train", "train"),
                    ("id_test", "test"),
                    ("ood_eval", "ood_genre"),
                )
            ] + [
                {
                    "role": "calibration_negatives",
                    "version_id": negatives["id"],
                    "split": "train",
                    "input_column": "text",
                    "label_column": "label",
                    "label_mapping": {"unlabelled": "negative"},
                    "negatives_basis": {
                        "kind": "assumed_negative",
                        "rule": "real text with the behaviour's screen applied",
                        "screen": [
                            {
                                "name": pin["rule_set_name"],
                                "version": pin["rule_set_version"],
                                "sha256": pin["rule_set_sha256"],
                            }
                        ],
                    },
                }
            ]

        main = await agent(
            "dataworks_create_detector_set",
            name=f"{key}-main",
            positive_meaning=record["positive_meaning"],
            behaviour_key=key,
            behaviour_definition=record,
            roles=roles(doc["label_column"], {"true": "positive", "false": "negative"}),
        )
        sq = data["sub_question"]
        sub = await agent(
            "dataworks_create_detector_set",
            name=f"{key}-{sq['column'].replace('_', '-')}",
            positive_meaning=record["positive_meaning"],
            behaviour_key=key,
            behaviour_definition=record,
            roles=roles(sq["column"], sq["mapping"]),
        )
        for made in (main, sub):
            checked = await agent("dataworks_check_detector_set", set_id=made["id"])
            refused = [o for o in checked["outcomes"] if o["outcome"] == "refused"]
            assert refused == [], json.dumps(refused, default=str)[:2000]

        # -- one send for both sets: each set's publish files are written first (008 builds, one
        #    label column at a time); the agent sends again until it waits for the operator's
        #    hub_push (FR-017.52)
        from tests.support.send_fixtures import repos_for

        pending_builds = 0
        for _ in range(4):
            try:
                asked = await agent(
                    "dataworks_send_detector_sets",
                    set_ids=[main["id"], sub["id"]],
                    namespace=NAMESPACE,
                    wait_for_builds_s=0,
                )
                break
            except ToolError as refused_send:
                assert "builds_pending" in str(refused_send), refused_send
                pending_builds += 1
                assert sender.publisher.run_publish_tasks(), "a pending build the worker ran"
        else:
            raise AssertionError("the send never stopped waiting for builds")
        assert pending_builds >= 1
        # what miStudio will count in each downloaded split (the fake's data: the files' values)
        repo = repos_for({"v": labelled["id"]})[labelled["id"]]
        for split_name in ("train", "test", "ood_genre"):
            for column in (doc["label_column"], sq["column"]):
                sender.mistudio.values_by_column[(repo, split_name, column)] = values_of(
                    data_dir, labelled, split_name, column
                )
        neg_repo = repos_for({"v": negatives["id"]})[negatives["id"]]
        sender.mistudio.values_by_column[(neg_repo, "train", "label")] = values_of(
            data_dir, negatives, "train", "label"
        )
        assert asked["status"] == "pending" and asked["action"] == "hub_push"
        await operator.approve(asked["approval_id"])
        with sync_session_factory()() as db:
            sends = list(db.query(DetectorSend).order_by(DetectorSend.created_at))
            jobs = [(s.id, s.job_id) for s in sends]
        assert len(jobs) == 2
        for send_id, job_id in jobs:
            result = sender.run(job_id)
            assert result["status"] == "completed", sender.send(send_id).error
            got = await agent("dataworks_get_detector_send", send_id=send_id)
            assert got["state"] == "completed"
        regs = sender.mistudio.calls("POST", REGISTER)
        # one view per set and role; the two sets share one calibration-negatives view (the same
        # version, split, column and mapping), registered once
        assert len(regs) == 7, [c.body["label_column"] for c in regs]
        assert Counter(c.body["label_column"] for c in regs) == {
            doc["label_column"]: 3,
            sq["column"]: 3,
            "label": 1,
        }
        assert {c.body["behaviour"] for c in regs} == {key}

        # -- the status reads complete, from the objects alone
        last = await agent("dataworks_get_behaviour_status", behaviour_key=key)
        assert last["complete"] is True and last["next"] is None, last["journey"]
        assert last["approvals_ahead"] == []
    finally:
        await agent.client.close()

    # 13.2 / 13.3: the call count per tool, the same for every behaviour but the per-genre
    # templates (the behaviour's own vocabulary decides how many)
    assert dict(agent.calls) == {
        "dataworks_get_behaviour_status": 2,
        "dataworks_resolve_behaviour": 1,
        "dataworks_save_rule_set": 1,
        "dataworks_save_generation_template": len(genres),
        "dataworks_save_recipe": 4,
        "dataworks_create_index_source": 1,
        "dataworks_upload_file": 1,
        "dataworks_job_status": 6,
        "dataworks_create_dataset": 1,
        "dataworks_build_version": 4,
        "dataworks_get_version": 4,
        "dataworks_start_generation": 2,
        "dataworks_get_generation_run": 2,
        "dataworks_save_template": 1,
        "dataworks_start_label_run": 1,
        "dataworks_get_approval_status": 1,
        "dataworks_get_label_run": 1,
        "dataworks_list_label_runs": 1,
        "dataworks_get_label_run_agreement": 1,
        "dataworks_review_disagreements": 1,
        "dataworks_create_detector_set": 2,
        "dataworks_check_detector_set": 2,
        "dataworks_send_detector_sets": pending_builds + 1,
        "dataworks_get_detector_send": 2,
    }
    # 13.4: the only REST the test made is the operator's
    assert operator.calls and all(OPERATOR_CALLS.match(c) for c in operator.calls), operator.calls
    assert sum(1 for c in operator.calls if c.endswith("/approve")) == 2
