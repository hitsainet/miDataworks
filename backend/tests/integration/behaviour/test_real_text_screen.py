"""Real text, the screen and calibration negatives, through real builds (017 FTASKS 7.3 – 7.7;
FR-017.7, FR-017.31, FR-017.32, FR-017.33).

A real-text source holds rows on which fixture-alpha's signal fires, rows on which fixture-beta's
fires, and clean rows; every fired row carries a SENTINEL string. ``rule_set@1`` runs each fixture's
rule set with ``use = screen``, then ``screen_filter@1`` drops what any declared screen flags. The
drop log, the row events, the job records and the captured logs never hold the sentinel (M11);
skipping the screen or ignoring part of the declared list turns the screen test red (M10).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pyarrow.parquet as pq
import pytest

from src.core.database import sync_session_factory
from tests.integration.behaviour.test_label_build import save_rule_set
from tests.integration.test_version_build import request, setup, src
from tests.support.label_fixtures import definition_text, vocabulary
from tests.support.operator_build_driver import RealDriver
from tests.support.version_fixtures import make_source

SENTINEL = "SENTINEL-6c1f2e"
ALPHA_ROW = f"{SENTINEL} wrote: the vexilloquant card 4111 1111 1111 1111 is on file."
BETA_ROW = f"{SENTINEL} said the quorbish was left behind at the station."
#: every boolean output of both screening rule sets (the basis proof asks for all of them)
FLAGS = ["sa_fa_hit", "sa_fa_cued", "sb_fb_label"]
#: the first flag each fired row is dropped on
FIRED = ["sa_fa_hit", "sb_fb_label"]


def corpus(clean: int = 12, length: int = 1) -> list[dict[str, Any]]:
    rows = [{"text": ALPHA_ROW, "label": "unlabelled"}, {"text": BETA_ROW, "label": "unlabelled"}]
    rows += [
        {"text": " ".join([f"A plain line about the weather, number {i}."] * length),
         "label": "unlabelled"}
        for i in range(clean)
    ]  # fmt: skip
    return rows


async def screen_steps(client: httpx.AsyncClient) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    pins = {k: await save_rule_set(client, k) for k in ("fixture-alpha", "fixture-beta")}
    steps = [
        {
            "operator": "rule_set",
            "version": "1",
            "params": {
                **pins[key],
                "use": "screen",
                "text_column": "text",
                "output_prefix": prefix,
            },
        }
        for key, prefix in (("fixture-alpha", "sa_"), ("fixture-beta", "sb_"))
    ]
    return steps, pins


async def build(
    client: httpx.AsyncClient,
    driver: RealDriver,
    steps: list[dict[str, Any]],
    inputs: list[dict[str, Any]],
    name: str,
) -> tuple[dict[str, Any], str]:
    ds, rev = await setup(client, {"format": "dw.recipe/v1", "steps": steps}, name=name)
    response = await request(client, ds, rev, inputs, seed=3)
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    status = driver.run(job_id)
    from src.models.job import Job
    from src.models.version import VersionBuild

    with sync_session_factory()() as db:
        job = db.get(Job, job_id)
        assert status == "completed", job.error if job else None
        built = db.get(VersionBuild, job_id)
        assert built is not None and built.version_id is not None
        version_id = str(built.version_id)
    return (await client.get(f"/api/v1/versions/{version_id}")).json(), job_id


def rows_of(data_dir: Path, version: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for split in version["splits"]:
        out.extend(pq.read_table(data_dir / split["path"]).to_pylist())
    return out


async def screened(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, flags: list[str], name: str
) -> tuple[dict[str, Any], str, str, dict[str, Any]]:
    source = make_source(data_dir, {"train": corpus()}, repo_id=f"org/{name}")
    steps, pins = await screen_steps(client)
    steps.append(
        {"operator": "screen_filter", "version": "1.0.0", "params": {"flag_columns": flags}}
    )
    version, job_id = await build(client, driver, steps, [src(source)], name)
    return version, job_id, source, pins


async def test_every_declared_screen_drops_its_rows_and_the_log_holds_no_text(
    client: httpx.AsyncClient,
    driver: RealDriver,
    data_dir: Path,
    operator_name: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """7.4, 7.5 (FR-017.32): both screening rule sets' rows are dropped and counted; the drop log
    names the row, its source, the signal and the reason; no record holds the row's text."""
    caplog.set_level(logging.DEBUG)
    version, job_id, source, _ = await screened(client, driver, data_dir, FLAGS, "screened")
    kept = rows_of(data_dir, version)
    assert len(kept) == 12 and all(SENTINEL not in r["text"] for r in kept)
    summary = (await client.get(f"/api/v1/versions/{version['id']}/drop-log")).json()
    events = (
        await client.get(
            f"/api/v1/versions/{version['id']}/events", params={"kind": "dropped", "limit": 100}
        )
    ).json()
    dropped = [e for e in events["items"] if e["reason_code"] == "screened_out"]
    assert sorted(e["statistic_name"] for e in dropped) == FIRED
    assert {e["statistic_text"] for e in dropped} == {f"source={source}"}
    from src.models.job import Job

    with sync_session_factory()() as db:
        job = db.get(Job, job_id)
        assert job is not None
        records = json.dumps(
            {"result": job.result, "progress": job.progress, "error": job.error,
             "params": job.params},  # fmt: skip
            default=str,
        )
    for where, text in (
        ("drop log", json.dumps(summary)),
        ("row events", json.dumps(events)),
        ("job record", records),
        ("logs", caplog.text),
    ):
        assert SENTINEL not in text, where
        assert "4111 1111 1111 1111" not in text and "quorbish was" not in text, where
    assert "screened_out" in json.dumps(summary)


async def test_a_second_screening_rule_set_drops_the_rows_it_fires_on(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """M10b: a screen listing only the first rule set keeps the second one's row."""
    only_alpha, _, _, _ = await screened(client, driver, data_dir, FLAGS[:1], "alpha-only")
    texts = [r["text"] for r in rows_of(data_dir, only_alpha)]
    assert BETA_ROW in texts and ALPHA_ROW not in texts
    both, _, _, _ = await screened(client, driver, data_dir, FLAGS, "both")
    assert BETA_ROW not in [r["text"] for r in rows_of(data_dir, both)]


async def test_a_flag_no_step_writes_is_refused(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    steps, _ = await screen_steps(client)
    steps.append(
        {"operator": "screen_filter", "version": "1.0.0", "params": {"flag_columns": ["sc_nope"]}}
    )
    validated = await client.post(
        "/api/v1/recipes/validate",
        json={"body": {"format": "dw.recipe/v1", "steps": steps}, "columns": ["text", "label"]},
    )
    body = validated.json()
    assert body["valid"] is False
    assert any(
        e["code"] == "missing_input_column" and "sc_nope" in e["message"]
        for s in body["steps"]
        for e in s["errors"]
    ), body


async def test_real_text_insertion_keeps_the_rows_source_rows(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """7.6 (FR-017.7): an element inserted into screened real text CHANGES the row: it stays
    ``origin = source`` and names its source as generator."""
    version, _, source, _ = await screened(client, driver, data_dir, FLAGS, "for-insertion")
    step = {
        "operator": "insert_element",
        "version": "1.0.0",
        "params": {
            "mode": "real_text",
            "element_column": "label",
            "vocabulary": vocabulary("fixture-beta"),
            "definition": definition_text("fixture-beta"),
            "real_text_values": json.dumps(
                {"genre": "memo", "kind": "request", "framing": "describes", "form": "prose"}
            ),
        },
    }
    child, _ = await build(
        client, driver, [step], [{"kind": "version", "version_id": version["id"]}], "inserted"
    )
    rows = rows_of(data_dir, child)
    assert len(rows) == 12
    for row in rows:
        assert row["_dw_origin"] == "source"
        assert row["generator"] == f"real_text:{source}"
        assert row["text"][row["ins_start"] : row["ins_end"]] == "unlabelled"


# --- calibration negatives (FR-017.33) -----------------------------------------------------------


def _set(
    name: str,
    key: str,
    version_id: str,
    basis: dict[str, Any],
    monitored: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name,
        "behaviour_key": key,
        "roles": [
            {
                "role": "calibration_negatives",
                "version_id": version_id,
                "split": "train",
                "input_column": "text",
                "label_column": "label",
                "label_mapping": {"unlabelled": "negative"},
                "negatives_basis": basis,
            }
        ],
    }
    if monitored is not None:
        body["monitored_ref"] = monitored
    return body


def _pin(pin: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": pin["rule_set_name"],
        "version": pin["rule_set_version"],
        "sha256": pin["rule_set_sha256"],
    }


async def _checks(client: httpx.AsyncClient, set_id: str) -> dict[str, dict[str, Any]]:
    response = await client.post(f"/api/v1/detector-sets/{set_id}/checks")
    assert response.status_code == 200, response.text
    return {o["code"]: o for o in response.json()["outcomes"]}


async def test_screened_negatives_name_the_screen_and_d5_reads_their_length(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """7.7 (fixture-alpha): the basis names the screen that ran in the version's build; a screen
    that did not run there is refused; a basis with no evidence is refused. D-5 is green against
    monitored text of the same length and notes long monitored text."""
    version, _, _, pins = await screened(client, driver, data_dir, FLAGS, "alpha-negatives")
    alpha = _pin(pins["fixture-alpha"])
    same = make_source(data_dir, {"train": corpus(20)}, repo_id="org/monitored-same")
    longer = make_source(data_dir, {"train": corpus(20, length=40)}, repo_id="org/monitored-long")
    monitored = {}
    for tag, source in (("same", same), ("long", longer)):
        v, _ = await build(
            client, driver, [{"operator": "fx_keep_all", "version": "1", "params": {}}],
            [src(source)], f"monitored-{tag}",
        )  # fmt: skip
        monitored[tag] = {
            "kind": "version",
            "version_id": v["id"],
            "split": "train",
            "column": "text",
        }
    basis = {"kind": "assumed_negative", "screen": [alpha]}
    created = await client.post(
        "/api/v1/detector-sets",
        json=_set("alpha-cal", "fixture-alpha", version["id"], basis, monitored["same"]),
    )
    assert created.status_code == 201, created.text
    role = created.json()["roles"][0]
    recorded = role["negatives_basis"]
    assert (recorded["kind"], recorded["screen"]) == ("assumed_negative", [alpha])
    assert "second_opinion" not in recorded  # absent evidence is not recorded as null
    outcomes = await _checks(client, created.json()["id"])
    assert outcomes["D-8"]["outcome"] == "note"
    assert f"screened by {alpha['name']}@{alpha['version']}" in outcomes["D-8"]["reason"]
    assert outcomes["D-5"]["outcome"] == "green", outcomes["D-5"]
    long_set = await client.post(
        "/api/v1/detector-sets",
        json=_set("alpha-cal-long", "fixture-alpha", version["id"], basis, monitored["long"]),
    )
    assert long_set.status_code == 201, long_set.text
    assert (await _checks(client, long_set.json()["id"]))["D-5"]["outcome"] == "note"
    # a screen the build did not run as a screen is unproven; no evidence at all is incomplete
    unproven = await client.post(
        "/api/v1/detector-sets",
        json=_set("alpha-unproven", "fixture-alpha", version["id"],
                  {"kind": "assumed_negative", "screen": [{**alpha, "version": 9}]}),
    )  # fmt: skip
    assert unproven.status_code == 422, unproven.text
    assert unproven.json()["error"]["code"] == "negatives_basis_unproven"
    from tests.integration.rules.conftest import live_tools

    assert unproven.json()["error"]["details"]["next"]["tool"] in await live_tools()
    assert unproven.json()["error"]["details"]["missing"] == [f"{alpha['name']}@9"]
    bare = await client.post(
        "/api/v1/detector-sets",
        json=_set("alpha-bare", "fixture-alpha", version["id"], {"kind": "assumed_negative"}),
    )
    assert bare.status_code == 422, bare.text
    assert bare.json()["error"]["code"] == "negatives_basis_incomplete"
    # a set with no behaviour keeps 009's rule: assumed negative alone is accepted
    plain = _set("plain-cal", "fixture-alpha", version["id"], {"kind": "assumed_negative"})
    plain.pop("behaviour_key")
    assert (await client.post("/api/v1/detector-sets", json=plain)).status_code == 201


async def test_a_behaviour_with_no_screen_names_its_second_opinion_sample(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """7.7 (fixture-beta): the basis names the second-opinion sample and its estimated positive
    rate; a sample below SECOND_OPINION_NO_RULE_NEGATIVES is refused naming the setting."""
    source = make_source(data_dir, {"train": corpus()[2:]}, repo_id="org/beta-negatives")
    version, _ = await build(
        client, driver, [{"operator": "fx_keep_all", "version": "1", "params": {}}],
        [src(source)], "beta-negatives",
    )  # fmt: skip
    watched = make_source(data_dir, {"train": corpus(20)}, repo_id="org/beta-monitored")
    monitored, _ = await build(
        client, driver, [{"operator": "fx_keep_all", "version": "1", "params": {}}],
        [src(watched)], "beta-monitored",
    )  # fmt: skip
    ref = {"kind": "version", "version_id": monitored["id"], "split": "train", "column": "text"}
    sample = {"sample_rows": 1000, "estimated_positive_rate": 0.004, "label_run_id": "lr_x"}
    basis = {"kind": "assumed_negative", "second_opinion": sample}
    created = await client.post(
        "/api/v1/detector-sets", json=_set("beta-cal", "fixture-beta", version["id"], basis, ref)
    )
    assert created.status_code == 201, created.text
    outcomes = await _checks(client, created.json()["id"])
    d8 = outcomes["D-8"]
    assert "1,000 rows" in d8["reason"] and "0.40%" in d8["reason"]
    assert outcomes["D-5"]["outcome"] == "green", outcomes["D-5"]
    short = {**basis, "second_opinion": {**sample, "sample_rows": 999}}
    refused = await client.post(
        "/api/v1/detector-sets", json=_set("beta-short", "fixture-beta", version["id"], short)
    )
    assert refused.status_code == 422, refused.text
    error = refused.json()["error"]
    assert error["code"] == "negatives_basis_incomplete"
    assert error["details"]["setting"] == "SECOND_OPINION_NO_RULE_NEGATIVES"
    assert error["details"]["next"]["tool"] == "dataworks_get_settings"
    wrong_kind = {"kind": "labeler_filtered", "labeler_identity_hash": "a" * 64, "rule": "r",
                  "second_opinion": sample}  # fmt: skip
    invalid = await client.post(
        "/api/v1/detector-sets", json=_set("beta-wrong", "fixture-beta", version["id"], wrong_kind)
    )
    assert invalid.status_code == 422, invalid.text


async def test_the_licence_facts_name_every_real_text_source_and_report_one_without_a_licence(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """7.3 (FR-017.31): a behaviour version built from two real-text sources (one stating no
    licence) through the screen: the facts every publish reads name both, and C-1 reports the
    unlicensed one (008's existing check, on a behaviour recipe)."""
    from src.models.version import Version
    from src.services.publishing import checks as publishing_checks
    from src.services.publishing.check_inputs import assemble, manifest_source

    stated = make_source(data_dir, {"train": corpus()}, repo_id="org/stated", licence="mit")
    unstated = make_source(data_dir, {"train": corpus(4)}, repo_id="org/unstated", licence=None)
    steps, _ = await screen_steps(client)
    steps.append(
        {"operator": "screen_filter", "version": "1.0.0", "params": {"flag_columns": FLAGS}}
    )
    version, _ = await build(client, driver, steps, [src(stated), src(unstated)], "two-sources")
    with sync_session_factory()() as db:
        row = db.get(Version, version["id"])
        assert row is not None
        assembled = assemble(db, row, label_column=None, visibility="public", token_scope="write")
        named = {f.source.id: manifest_source(f) for f in assembled.sources}
        c1, *_ = publishing_checks.c1_licences(assembled.inputs)
    assert set(named) == {stated, unstated}
    assert named[unstated]["licence"]["displayed"] == "not stated"
    assert c1.check == "C-1" and c1.outcome.value != "green", c1
    assert unstated in c1.evidence["sources"] and stated not in c1.evidence["sources"]


# --- Review R3: a screen that RAN is not a screen that removed anything ---------------------------


async def _unproven_reason(
    client: httpx.AsyncClient, name: str, version_id: str, alpha: dict[str, Any]
) -> str:
    response = await client.post(
        "/api/v1/detector-sets",
        json=_set(name, "fixture-alpha", version_id,
                  {"kind": "assumed_negative", "screen": [alpha]}),
    )  # fmt: skip
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "negatives_basis_unproven", error
    assert error["details"]["next"]["tool"] == "dataworks_get_version"
    return str(error["message"])


async def test_a_screen_whose_flags_no_filter_drops_does_not_prove_the_negatives(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """R3 F1: the screen ran (use = screen, the exact pin) but nothing dropped what it flagged:
    no filter at all, or a filter that ignores one of its flags. The positive row is still in the
    version, so the basis is refused; the fully filtered build is accepted (the builder's test)."""
    source = make_source(data_dir, {"train": corpus()}, repo_id="org/unfiltered")
    steps, pins = await screen_steps(client)
    alpha = _pin(pins["fixture-alpha"])
    bare, _ = await build(client, driver, list(steps), [src(source)], "no-filter")
    assert ALPHA_ROW in [r["text"] for r in rows_of(data_dir, bare)]  # the positive survived
    assert "screen_filter" in await _unproven_reason(client, "r3-no-filter", bare["id"], alpha)
    partial = [
        *steps,
        {"operator": "screen_filter", "version": "1.0.0",
         "params": {"flag_columns": ["sa_fa_cued", "sb_fb_label"]}},
    ]  # fmt: skip
    version, _ = await build(client, driver, partial, [src(source)], "partial-filter")
    reason = await _unproven_reason(client, "r3-partial", version["id"], alpha)
    assert "sa_fa_hit" in reason


async def test_a_screen_used_as_a_label_or_over_another_column_does_not_prove_the_negatives(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """R3 F1: the pinned rule set ran with use = label (not a screen), or screened a column that
    is not the role's input column; neither screened the role's text."""
    rows = [{**r, "title": "a plain title"} for r in corpus()]
    source = make_source(data_dir, {"train": rows}, repo_id="org/other-column")
    steps, pins = await screen_steps(client)
    alpha = _pin(pins["fixture-alpha"])
    filtered = {"operator": "screen_filter", "version": "1.0.0",
                "params": {"flag_columns": FLAGS}}  # fmt: skip
    as_label = [{**s, "params": {**s["params"], "use": "label"}} for s in steps]
    labelled, _ = await build(client, driver, [*as_label, filtered], [src(source)], "as-label")
    reason = await _unproven_reason(client, "r3-as-label", labelled["id"], alpha)
    assert "use = screen" in reason
    other = [{**s, "params": {**s["params"], "text_column": "title"}} for s in steps]
    titled, _ = await build(client, driver, [*other, filtered], [src(source)], "other-column")
    assert ALPHA_ROW in [r["text"] for r in rows_of(data_dir, titled)]
    reason = await _unproven_reason(client, "r3-other-column", titled["id"], alpha)
    assert "'title'" in reason and "'text'" in reason


async def test_rows_changed_after_the_screen_are_not_proven_screened(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """R3 F1: a step after the screen that changes the text means the negatives' text is not
    the text the screen read."""
    source = make_source(data_dir, {"train": corpus()}, repo_id="org/changed-after")
    steps, pins = await screen_steps(client)
    steps += [
        {"operator": "screen_filter", "version": "1.0.0", "params": {"flag_columns": FLAGS}},
        {"operator": "fx_mapper_upper", "version": "1", "params": {}},
    ]
    version, _ = await build(client, driver, steps, [src(source)], "changed-after")
    reason = await _unproven_reason(
        client, "r3-changed", version["id"], _pin(pins["fixture-alpha"])
    )
    assert "changed or added rows after the screen" in reason


async def test_a_key_given_later_checks_the_saved_negatives_basis(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """R3 F5: a set saved WITHOUT a key keeps 009's rule; giving it a behaviour key later binds
    its saved roles to FR-017.33, so a bare assumed-negative basis is refused and the key is not
    stored. A proven screen passes."""
    version, _, _, pins = await screened(client, driver, data_dir, FLAGS, "keyed-later")
    plain = _set("r3-keyed-later", "fixture-alpha", version["id"], {"kind": "assumed_negative"})
    plain.pop("behaviour_key")
    created = await client.post("/api/v1/detector-sets", json=plain)
    assert created.status_code == 201, created.text
    set_id = created.json()["id"]
    keyed = await client.patch(
        f"/api/v1/detector-sets/{set_id}", json={"behaviour_key": "fixture-alpha"}
    )
    assert keyed.status_code == 422, keyed.text
    assert keyed.json()["error"]["code"] == "negatives_basis_incomplete"
    assert (await client.get(f"/api/v1/detector-sets/{set_id}")).json()["behaviour_key"] is None
    proven = _set("r3-keyed-later-ok", "fixture-alpha", version["id"],
                  {"kind": "assumed_negative", "screen": [_pin(pins["fixture-alpha"])]})  # fmt: skip
    proven.pop("behaviour_key")
    ok = await client.post("/api/v1/detector-sets", json=proven)
    assert ok.status_code == 201, ok.text
    again = await client.patch(
        f"/api/v1/detector-sets/{ok.json()['id']}", json={"behaviour_key": "fixture-alpha"}
    )
    assert again.status_code == 200, again.text
