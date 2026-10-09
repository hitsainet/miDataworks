"""Results read-back and reward marks (FTASKS 7.1 - 7.5, 8.1, 8.3, 8.4; FR-009.31 - FR-009.41,
FR-009.69 - FR-009.71).

The fake miStudio serves run C's recorded report (``pm_935fc9088482``), re-keyed to the probe
dataset IDs this send registered — so the figures must come back through 009's recorded IDs."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import httpx

from tests.support.fake_mistudio import FIXTURES
from tests.support.send_fixtures import API, SendDriver, ready_set, start_send


async def sent_set(client: httpx.AsyncClient, sender: SendDriver) -> tuple[str, dict[str, Any]]:
    set_id, _, _ = await ready_set(client, sender)
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "completed"
    send = (await client.get(f"{API}/detector-sends/{started['send_id']}")).json()
    return set_id, send


def serve_run_c(
    sender: SendDriver, send: dict[str, Any], probe_id: str = "pm_935fc9088482"
) -> None:
    skeleton = send["run_request"]
    names = {
        s["probe_dataset_id"]: next(
            r["view_name"] for r in send["snapshot"]["roles"] if r["id"] == s["unit_key"]
        )
        for s in send["steps"]
        if s["step"] == "register"
    }
    report = json.loads((FIXTURES / "report_pm_935fc9088482.json").read_text())
    report = copy.deepcopy(report)
    report["probe"]["id"] = probe_id
    test_view, ood_view = skeleton["eval_dataset_ids"]
    old_test, old_ood = (e["dataset_id"] for e in report["evaluations"])
    for e in report["evaluations"]:
        e["dataset_id"] = test_view if e["dataset_id"] == old_test else ood_view
        e["metrics"]["name"] = names[e["dataset_id"]]
    report["threshold_transfer"]["per_set"][0]["name"] = names[test_view]
    report["threshold_transfer"]["per_set"][1]["name"] = names[ood_view]
    run = {
        "id": "pmr_a2bcffdcd695",
        "model_id": "m_40e78d80",
        "train_dataset_id": skeleton["train_dataset_id"],
        "eval_dataset_ids": skeleton["eval_dataset_ids"],
        "calibration_dataset_id": skeleton["calibration_dataset_id"],
        "status": "completed",
        "config": {"layers": [12]},
        "environment": {"model_dtype": "bfloat16"},
    }
    other = {**run, "id": "pmr_other", "train_dataset_id": "pmd_someone_else"}
    sender.mistudio.runs = [run, other]
    sender.mistudio.probes = {"pmr_a2bcffdcd695": [{"id": probe_id}]}
    sender.mistudio.reports = {probe_id: report}


async def test_refresh_reproduces_run_c_through_recorded_ids(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    set_id, send = await sent_set(client, sender)
    serve_run_c(sender, send)
    response = await client.post(f"{API}/detector-sets/{set_id}/results/refresh")
    assert response.status_code == 200, response.text
    snap = response.json()
    assert [r["id"] for r in snap["runs"]] == [
        "pmr_a2bcffdcd695"
    ]  # the other run is not this set's
    [figure] = snap["evaluations"]
    assert figure["rung_language"] == "detects on unseen tasks"
    assert figure["rung_next_step"] == "run the judge baseline on the same out-of-distribution sets"
    ood = next(s for s in figure["sets"] if s["role"] == "ood_eval")
    assert round(ood["auroc"], 4) == 0.7375 and [round(x, 4) for x in ood["ci"]] == [0.7258, 0.7502]
    assert round(ood["firing"]["positives_firing"], 3) == 0.363
    assert ood["firing"]["n_positive"] == 2661
    assert ood["paired"]["available"] is False and "T-47" in ood["paired"]["reason"]
    assert figure["caveats"]["threshold_transfer_caution"]
    assert sender.mistudio.count("GET", "/api/v1/probe-monitors/runs") == 1
    assert sender.mistudio.calls("GET", "/api/v1/probe-monitors/runs")[0].query == {"limit": "200"}
    got = (await client.get(f"{API}/detector-sets/{set_id}/results")).json()
    assert got["id"] == snap["id"]


async def test_a_probe_deleted_between_refreshes_stays_marked(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    set_id, send = await sent_set(client, sender)
    serve_run_c(sender, send)
    first = (await client.post(f"{API}/detector-sets/{set_id}/results/refresh")).json()
    sender.mistudio.reports = {}
    second = (await client.post(f"{API}/detector-sets/{set_id}/results/refresh")).json()
    assert second["gone"]["probes"] == ["pm_935fc9088482"]
    [kept] = second["evaluations"]
    assert kept["gone"] is True and kept["label"] == "no longer in miStudio"
    assert kept["sets"] == first["evaluations"][0]["sets"]


async def test_mistudio_unreachable_returns_502_and_keeps_the_snapshot(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    set_id, send = await sent_set(client, sender)
    serve_run_c(sender, send)
    first = (await client.post(f"{API}/detector-sets/{set_id}/results/refresh")).json()
    sender.mistudio.fail("GET /probe-monitors/runs", drop=True)
    failed = await client.post(f"{API}/detector-sets/{set_id}/results/refresh")
    assert failed.status_code == 502 and failed.json()["error"]["code"] == "mistudio_unreachable"
    sender.mistudio.fail("GET /probe-monitors/runs", non_json=True)
    not_json = await client.post(f"{API}/detector-sets/{set_id}/results/refresh")
    assert not_json.status_code == 502 and not_json.json()["error"]["code"] == "mistudio_not_json"
    assert (await client.get(f"{API}/detector-sets/{set_id}/results")).json()["id"] == first["id"]


async def test_a_reward_marked_probe_is_never_an_evaluation(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    set_id, send = await sent_set(client, sender)
    serve_run_c(sender, send)
    marked = await client.post(
        f"{API}/reward-marks",
        json={"mistudio_probe_id": "pm_935fc9088482", "reason": "miForge reward"},
    )
    assert marked.status_code == 201, marked.text
    again = await client.post(
        f"{API}/reward-marks", json={"mistudio_probe_id": "pm_935fc9088482", "reason": "twice"}
    )
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_marked"
    snap = (await client.post(f"{API}/detector-sets/{set_id}/results/refresh")).json()
    assert snap["evaluations"] == []
    [reward] = snap["training_reward"]
    assert reward["group"] == "training reward - not an evaluation"
    assert reward["evaluation_slot"]["judge"]["allowed"] is True
    listed = (await client.get(f"{API}/reward-marks")).json()["items"]
    assert [m["mistudio_probe_id"] for m in listed] == ["pm_935fc9088482"]
    # no unmark route exists (TQ9)
    assert (await client.delete(f"{API}/reward-marks/{listed[0]['id']}")).status_code in (404, 405)
