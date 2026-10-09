"""Probe-verdict label runs end to end (009 FTASKS 12.4 as reinterpreted, 12.5, 12.6, 12.7;
FR-009.45 - FR-009.51, FR-009.77, FR-009.81, FR-009.83; operator decision 2026-10-07).

What runs for real: 009's send (008's publish worker, the fake Hub, the fake miStudio), the results
refresh that records miStudio's figures, 005's plan, start, worker, lease holder and label store,
and the probe client. Only miLLM's HTTP is the fake (``tests/support/fake_miLLM.py``), serving
``POST /api/probes/score`` and ``GET /api/probes[/{id}]`` with miLLM 44e4c4a's bodies and refusals.

The reproduction gate's target is the set's in-distribution test role (60 rows, 30/30), whose
recorded miStudio AUROC is 0.9751 [0.9652, 0.9837] (run C's report). ``gate_score`` puts ONE
positive below 20 of the 30 negatives: 880 of 900 pairs, AUROC 0.9778 — inside the interval.
A perfect separator (AUROC 1.0) is OUTSIDE it, which is the failing case.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from src.core.config import get_settings
from src.core.database import sync_session_factory
from src.models.label import Label
from tests.integration.detector_sets.test_results_and_marks import sent_set, serve_run_c
from tests.support.fake_millm import ORIGIN, probe_row
from tests.support.labeling_fixtures import Labeling, labeling, make_version  # noqa: F401
from tests.support.send_fixtures import API, SendDriver

LLAMA = {
    "id": 3,
    "name": "Llama-3.1-8B-Instruct",
    "repo_id": "meta-llama/Llama-3.1-8B-Instruct",
    "revision": "0e9e39f249a16976918f6564b8830bc894c89659",
    "quantization": "FP16",
}
MISTUDIO_PROBE = "pm_935fc9088482"
PROBE = "pr_humor"


def gate_score(text: str) -> float:
    """Scores for the gate's id_test rows (``te {i} ...``, odd i humorous) and the run's rows."""
    words = text.split()
    tag, i = words[0], int(words[1])
    if tag == "te":
        if i % 2 == 1:
            return 19.5 if i == 1 else 100.0 + i
        return float(i)
    return 2.5 if i == 0 else float(i % 7) - 1.0  # row 0 scores exactly on the bar


def perfect(text: str) -> float:
    words = text.split()
    return 100.0 + int(words[1]) if int(words[1]) % 2 == 1 else float(int(words[1]))


@pytest.fixture
def millm(labeling: Labeling, monkeypatch: pytest.MonkeyPatch) -> Labeling:  # noqa: F811
    labeling.millm.resident = dict(LLAMA)
    labeling.millm.probes = {
        PROBE: probe_row(PROBE, hf_id=LLAMA["repo_id"], mistudio_probe_id=MISTUDIO_PROBE)
    }
    labeling.millm.probe_score = gate_score
    monkeypatch.setattr(get_settings(), "millm_base_url", ORIGIN)
    return labeling


async def refreshed(client: httpx.AsyncClient, sender: SendDriver) -> tuple[str, dict[str, Any]]:
    set_id, send = await sent_set(client, sender)
    serve_run_c(sender, send, probe_id=MISTUDIO_PROBE)
    response = await client.post(f"{API}/detector-sets/{set_id}/results/refresh")
    assert response.status_code == 200, response.text
    return set_id, send


def body(version_id: str, **over: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "input_version_id": version_id,
        "role": "probe",
        "probe": {"probe_id": PROBE, "window": "all"},
        "field_map": {"text": "text"},
    }
    out.update(over)
    return out


async def plan(client: httpx.AsyncClient, b: dict[str, Any]) -> httpx.Response:
    return await client.get(f"{API}/label-runs/plan", params={"request": json.dumps(b)})


def rows(n: int, tag: str = "row") -> list[str]:
    return [f"{tag} {i} a headline" for i in range(n)]


def labels_of(run_id: str) -> dict[str, Label]:
    with sync_session_factory()() as db:
        found = db.execute(select(Label).where(Label.label_run_id == run_id)).scalars().all()
        for label in found:
            db.expunge(label)
        return {label.row_key: label for label in found}


async def test_a_probe_verdict_run_scores_every_row_once_after_the_gate(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    millm: Labeling,
    sender: SendDriver,  # after millm: the publisher's send_task patch must be the last one
) -> None:
    await refreshed(client, sender)
    version_id = make_version(data_dir, rows(30))
    planned = await plan(client, body(version_id))
    assert planned.status_code == 200, planned.text
    p = planned.json()
    assert p["rows_to_score"] == 30 and p["resident_model"] == LLAMA["name"]
    assert p["probe"]["probe_id"] == PROBE and p["probe"]["mistudio_probe_id"] == MISTUDIO_PROBE
    assert p["probe"]["preflight"]["checked"] is True
    assert p["reproduction"]["state"] == "will_run" and p["reproduction"]["role"] == "id_test"
    assert p["reproduction"]["mistudio_auroc"] == pytest.approx(0.9750930056392559)
    assert p["labeler_identity"] == {
        "protocol": "millm_probe_score",
        "model_id": LLAMA["name"],
        "model_revision": LLAMA["revision"],
        "probe_id": PROBE,
        "mistudio_probe_id": MISTUDIO_PROBE,
        "threshold_revision": 1,
        "window": "all",
        "input_form": "one_user_turn",
        "sent": "one input per request",
    }
    plan_calls = millm.millm.probe_score_calls  # the plan's one-input preflight
    assert plan_calls == 1

    started = await client.post(f"{API}/label-runs", json=body(version_id))
    assert started.status_code == 201, started.text
    run = started.json()
    assert run["kind"] == "probe_verdict" and run["endpoint_snapshot"]["probe"]["window"] == "all"
    final = millm.run_until_done(run["id"])
    assert final.state == "completed", final.error
    gate = final.endpoint_snapshot["reproduction"]
    assert gate["state"] == "passed" and gate["millm_auroc"] == pytest.approx(880 / 900, abs=1e-6)
    assert gate["rows_scored"] == 60 and gate["rows_dropped"] == 0
    # one request per row: 60 gate rows + 30 labeled rows (and the plan's preflight)
    score_calls = millm.millm.calls("/api/probes/score", "POST")
    assert len(score_calls) == 1 + 60 + 30 + 1  # plan + gate + rows + the start's re-plan
    for call in score_calls:
        assert len(call.body["inputs"]) == 1
        [item] = call.body["inputs"]
        assert set(item) == {"messages"} and item["messages"][0]["role"] == "user"
        assert len(item["messages"]) == 1
        assert call.body["return_token_ids"] is True
        assert call.body["probe_ids"] == [PROBE] and call.body["windows"] == ["all"]
        assert call.headers["x-millm-strict"] == "true"
        assert call.headers["x-millm-load-policy"] == "refuse"
    leased = [c for c in score_calls[2:] if "x-millm-lease" in c.headers]
    assert len(leased) == 90  # every worker call carries the lease (P-05, no approval)
    assert final.pinned is True and final.endpoint_snapshot["pinned"] is True
    assert len(millm.millm.calls("/api/models/3/lease", "POST")) == 1
    assert len(millm.millm.calls("/api/models/3/lease", "DELETE")) == 1
    got = labels_of(run["id"])
    assert len(got) == 30
    from tests.support.labeling_fixtures import row_key

    on_bar = got[row_key("row 0 a headline")]
    assert on_bar.outcome == "positive" and on_bar.parsed_value["score"] == 2.5
    assert on_bar.parsed_value["threshold"] == 2.5 and on_bar.probability is None
    assert on_bar.steering_state == "unsteered (scoring mode)"
    assert on_bar.parsed_value["token_ids"] and on_bar.parsed_value["sent_singly"] is True
    below = got[row_key("row 1 a headline")]  # score 0.0
    assert below.outcome == "negative"
    assert final.endpoint_snapshot["millm_model"] == {
        "hf_id": LLAMA["repo_id"],
        "revision": LLAMA["revision"],
        "dtype": "bfloat16",
        "quantization": "FP16",
    }

    # A second run of the same check reuses the recorded pass: no gate rows are scored again.
    version_b = make_version(data_dir, rows(5, tag="more"))
    before = millm.millm.probe_score_calls
    second = await client.post(f"{API}/label-runs", json=body(version_b))
    assert second.status_code == 201, second.text
    done = millm.run_until_done(second.json()["id"])
    assert done.state == "completed", done.error
    assert done.endpoint_snapshot["reproduction"]["cached_from_run_id"] == run["id"]
    assert millm.millm.probe_score_calls - before == 5 + 1  # rows + the start's preflight


async def test_a_gate_that_does_not_reproduce_refuses_naming_both_figures(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    millm: Labeling,
    sender: SendDriver,  # after millm: the publisher's send_task patch must be the last one
) -> None:
    await refreshed(client, sender)
    millm.millm.probe_score = perfect  # AUROC 1.0, above miStudio's 0.9837
    version_id = make_version(data_dir, rows(10))
    started = await client.post(f"{API}/label-runs", json=body(version_id))
    assert started.status_code == 201, started.text
    final = millm.run_until_done(started.json()["id"])
    assert final.state == "failed"
    assert final.error["code"] == "REPRODUCTION_FAILED"
    assert "1.0000" in final.error["message"] and "0.9751" in final.error["message"]
    assert "[0.9652, 0.9837]" in final.error["message"]
    assert labels_of(final.id) == {}
    assert final.endpoint_snapshot["reproduction"]["state"] == "failed"


async def test_no_recorded_evaluation_refuses_at_the_plan(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, millm: Labeling
) -> None:
    version_id = make_version(data_dir, rows(3))
    response = await plan(client, body(version_id))
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "REPRODUCTION_UNAVAILABLE" and MISTUDIO_PROBE in error["message"]


@pytest.mark.parametrize(
    ("arrange", "status", "code", "said"),
    [
        (lambda m: m.probes.clear(), 409, "PROBE_NOT_IMPORTED", "No probe pr_humor"),
        (lambda m: setattr(m, "engine", "llama.cpp"), 409, "PROBE_GGUF_UNSUPPORTED",
         "exposes no module tree"),
        (lambda m: setattr(m, "loaded_dtype", "float16"), 409, "PROBE_IDENTITY_MISMATCH",
         "wrong precision"),
        (lambda m: setattr(m, "resident", None), 409, "MODEL_NOT_LOADED", "No model is loaded"),
        (lambda m: m.resident.update(repo_id="google/gemma-2-2b-it"), 409, "MODEL_NOT_RESIDENT",
         "google/gemma-2-2b-it"),
    ],
)  # fmt: skip
async def test_the_plan_refuses_with_millms_reason(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    millm: Labeling,
    arrange: Any,
    status: int,
    code: str,
    said: str,
) -> None:
    arrange(millm.millm)
    version_id = make_version(data_dir, rows(3))
    response = await plan(client, body(version_id))
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code and said in error["message"], error


@pytest.mark.parametrize(
    ("arrange", "code", "said"),
    [
        (lambda m: m.probes.clear(), "PROBE_NOT_IMPORTED", "No probe pr_humor"),
        (lambda m: setattr(m, "engine", "llama.cpp"), "PROBE_GGUF_UNSUPPORTED",
         "exposes no module tree"),
        (lambda m: setattr(m, "loaded_dtype", "float16"), "PROBE_IDENTITY_MISMATCH",
         "wrong precision"),
        (lambda m: m.probes["pr_humor"]["definition"]["model"].update(hf_id="other/model"),
         "PROBE_IDENTITY_MISMATCH", "fitted on a different model"),
    ],
)  # fmt: skip
async def test_the_worker_refuses_with_millms_reason(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    millm: Labeling,
    sender: SendDriver,  # after millm: the publisher's send_task patch must be the last one
    arrange: Any,
    code: str,
    said: str,
) -> None:
    await refreshed(client, sender)
    version_id = make_version(data_dir, rows(4))
    started = await client.post(f"{API}/label-runs", json=body(version_id))
    assert started.status_code == 201, started.text
    arrange(millm.millm)  # miLLM changes between the start and the worker
    final = millm.run_until_done(started.json()["id"])
    assert final.state == "failed" and final.error["code"] == code, final.error
    assert said in final.error["message"]
    assert labels_of(final.id) == {}


async def test_a_server_without_a_lease_surface_runs_unpinned_and_says_so(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    millm: Labeling,
    sender: SendDriver,  # after millm: the publisher's send_task patch must be the last one
) -> None:
    await refreshed(client, sender)
    millm.millm.lease_supported = False
    version_id = make_version(data_dir, rows(4))
    started = await client.post(f"{API}/label-runs", json=body(version_id))
    assert started.status_code == 201, started.text
    final = millm.run_until_done(started.json()["id"])
    assert final.state == "completed", final.error
    assert final.pinned is False and final.endpoint_snapshot["pinned"] is False
    assert final.endpoint_snapshot["unpinned_reason"] == "this miLLM serves no lease routes"
    assert not any(
        "x-millm-lease" in c.headers for c in millm.millm.calls("/api/probes/score", "POST")
    )


async def test_provisional_null_and_untokenizable_rows(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    millm: Labeling,
    sender: SendDriver,  # after millm: the publisher's send_task patch must be the last one
) -> None:
    await refreshed(client, sender)
    texts = ["row 0 a headline", "row 3 UNTOKENIZABLE", "row 4 silent"]

    def score(text: str) -> float | None:
        return None if "silent" in text else gate_score(text)

    millm.millm.probe_score = score
    version_id = make_version(data_dir, texts)
    started = await client.post(f"{API}/label-runs", json=body(version_id))
    assert started.status_code == 201, started.text
    final = millm.run_until_done(started.json()["id"])
    assert final.state == "completed", final.error
    from tests.support.labeling_fixtures import row_key

    got = labels_of(final.id)
    assert got[row_key(texts[1])].outcome == "skipped"
    assert got[row_key(texts[1])].skip_reason == "TOKENIZATION_FAILED"
    assert got[row_key(texts[2])].outcome == "skipped"
    assert got[row_key(texts[2])].skip_reason == "no_scored_tokens"
    assert final.counts == {"positive": 1, "skipped": 2}

    # A window that placed no bar of its own is provisional: excluded, flagged, kept out.
    version_b = make_version(data_dir, ["row 0 again"])
    response = await client.post(
        f"{API}/label-runs", json=body(version_b, probe={"probe_id": PROBE, "window": "response"})
    )
    assert response.status_code == 201, response.text
    done = millm.run_until_done(response.json()["id"])
    assert done.state == "completed", done.error
    [label] = labels_of(done.id).values()
    assert label.outcome == "excluded" and label.provisional is True


async def test_probe_fields_that_do_not_apply_are_refused(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, millm: Labeling
) -> None:
    version_id = make_version(data_dir, rows(2))
    response = await plan(client, body(version_id, question="Is it funny?"))
    assert (
        response.status_code == 422 and response.json()["error"]["code"] == "PROBE_FIELDS_INVALID"
    )
    response = await plan(client, body(version_id, probe=None))
    assert response.status_code == 422 and response.json()["error"]["code"] == "PROBE_REQUIRED"
    response = await plan(client, body(version_id, field_map={"body": "text"}))
    assert response.status_code == 422 and response.json()["error"]["code"] == "FIELD_MAP_INVALID"


async def test_the_probe_list_reads_millm(
    client: httpx.AsyncClient, operator_name: str, millm: Labeling
) -> None:
    response = await client.get(f"{API}/labeling/probes")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["resident_model"] == LLAMA["repo_id"]
    [item] = data["items"]
    assert item["probe_id"] == PROBE and item["fits_resident_model"] is True
    assert item["window_thresholds"] == {"all": 2.5}
    assert len(millm.millm.calls("/api/probes", "GET")) == 1


async def test_the_millm_scores_feed_the_paired_reader(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    millm: Labeling,
    sender: SendDriver,  # after millm: the publisher's send_task patch must be the last one
) -> None:
    """A completed probe-verdict run over the OOD role's version is the per-row source the
    results panel reads (T-47): it is found by the SOURCE miStudio probe ID in the identity, and
    its scores are judged against miStudio's interval. These scores do not separate the classes,
    so the paired score is refused with the AUROC named — not "no source"."""
    set_id, send = await refreshed(client, sender)
    ood = next(r for r in send["snapshot"]["roles"] if r["role"] == "ood_eval")
    started = await client.post(f"{API}/label-runs", json=body(ood["version_id"]))
    assert started.status_code == 201, started.text
    final = millm.run_until_done(started.json()["id"])
    assert final.state == "completed", final.error
    assert final.labeler_identity["mistudio_probe_id"] == MISTUDIO_PROBE
    snap = (await client.post(f"{API}/detector-sets/{set_id}/results/refresh")).json()
    [figure] = snap["evaluations"]
    entry = next(s for s in figure["sets"] if s["role"] == "ood_eval")
    reason = entry["paired"]["reason"]
    assert reason.startswith("AUROC") and "0.7258" in reason, reason


async def test_agent_probe_runs_count_in_the_ledger_and_wait_over_the_threshold(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    millm: Labeling,
    sender: SendDriver,  # after millm: the publisher's send_task patch must be the last one
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-009.74 / P-07: an agent's probe-verdict rows count under run kind ``probe_verdict`` and
    a run that would take the version over the threshold waits for approval (202)."""
    from src.models.agent_label_row import AgentLabelRow

    await refreshed(client, sender)
    monkeypatch.setattr(get_settings(), "agent_label_row_threshold", 6)
    agent = {"X-Dataworks-Agent": "agent:claude"}
    version_id = make_version(data_dir, rows(4))
    first = await client.post(f"{API}/label-runs", json=body(version_id), headers=agent)
    assert first.status_code == 201, first.text
    with sync_session_factory()() as db:
        [row] = db.execute(select(AgentLabelRow)).scalars().all()
        assert (row.run_kind, row.rows_counted, row.run_id) == (
            "probe_verdict",
            4,
            first.json()["id"],
        )
    other = make_version(data_dir, rows(7, tag="big"))
    gated = await client.post(f"{API}/label-runs", json=body(other), headers=agent)
    assert gated.status_code == 202, gated.text
    assert gated.json()["status"] == "pending" and gated.json()["action"] == "agent_label_rows"


# --- the hard-negative miner over a finished probe-verdict run (FTASKS 14.1, 14.2) -----------


def miner_ctx(table: Any, run_id: str) -> Any:
    from src.operators.context import RunContext
    from src.operators.manifest import manifest_hash
    from src.operators.native.detector.hard_negative_miner import MANIFEST

    ctx = RunContext(
        MANIFEST, manifest_hash(MANIFEST), 1, None, {"text": "content"}, "dw.rowkey/v1",
        bindings=[{"kind": "label_run", "id": run_id}],
    )  # fmt: skip
    ctx.input_reader = lambda columns: iter(table.to_batches())
    return ctx


async def test_the_miner_keeps_rows_near_the_bar_and_rows_the_probe_gets_wrong(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    millm: Labeling,
    sender: SendDriver,  # after millm: the publisher's send_task patch must be the last one
) -> None:
    import pyarrow as pa

    from src.operators.errors import OperatorError
    from src.operators.native.detector.hard_negative_miner import HardNegativeMiner
    from tests.support.labeling_fixtures import row_key

    await refreshed(client, sender)
    texts = rows(12)
    version_id = make_version(data_dir, texts)
    started = await client.post(f"{API}/label-runs", json=body(version_id))
    run = millm.run_until_done(started.json()["id"])
    assert run.state == "completed", run.error
    table = pa.table(
        {
            "_dw_row_key": [row_key(t) for t in texts],
            "_dw_occurrence": [0] * 12,
            "text": texts,
            "label": ["yes" if i % 2 == 0 else "no" for i in range(12)],
        }
    )
    near = {"probe_label_run_id": run.id, "mode": "near", "band_below": 1.0, "band_above": 1.0}
    out = HardNegativeMiner().run(table.slice(0, 0), near, miner_ctx(table, run.id))
    # distances: row 0 is ON the bar (0.0); rows 3, 10 at -0.5 and 4, 11 at +0.5
    kept = set(out.output.column("text").to_pylist())
    assert kept == {"row 0 a headline", "row 3 a headline", "row 4 a headline",
                    "row 10 a headline", "row 11 a headline"}  # fmt: skip
    dropped = {e.row_key: e for e in out.events}
    one = dropped[row_key("row 1 a headline")]
    assert one.reason_code == "outside_band" and one.statistic_name == "distance"
    assert one.statistic_value == -2.5 and "score=0.0 threshold=2.5" in (one.statistic_text or "")
    stats = {s["row_key"]: s for s in out.report["kept"]}
    assert stats[row_key("row 0 a headline")]["distance"] == 0.0

    capped = HardNegativeMiner().run(
        table.slice(0, 0), {**near, "cap": 3}, miner_ctx(table, run.id)
    )
    assert capped.output.num_rows == 3
    assert row_key("row 0 a headline") in capped.output.column("_dw_row_key").to_pylist()
    assert sum(e.reason_code == "over_cap" for e in capped.events) == 2

    wrong = HardNegativeMiner().run(
        table.slice(0, 0),
        {
            "probe_label_run_id": run.id,
            "mode": "wrong",
            "reference_kind": "label_column",
            "reference_column": "label",
            "reference_positive_values": ["yes"],
            "reference_negative_values": ["no"],
        },
        miner_ctx(table, run.id),
    )
    # positives (score >= 2.5): rows 0, 4, 5, 6, 11; the reference calls even rows positive
    assert set(wrong.output.column("text").to_pylist()) == {
        f"row {i} a headline" for i in (2, 5, 8, 10, 11)
    }
    assert {e.reason_code for e in wrong.events} == {"correct"}

    with pytest.raises(OperatorError) as unbound:
        HardNegativeMiner().run(table.slice(0, 0), near, miner_ctx(table, "lr_other"))
    assert unbound.value.code == "label_run_not_bound"


async def test_a_role_mined_from_this_sets_evaluation_data_is_refused_by_d4(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    millm: Labeling,
    sender: SendDriver,  # after millm: the publisher's send_task patch must be the last one
) -> None:
    """FR-009.59: the training version's recipe mined a probe-verdict run over the set's OWN
    out-of-distribution role, so D-4 treats it as overlapping that role and refuses."""
    from sqlalchemy import text

    from src.models.version import Version
    from tests.support import db_factories

    set_id, send = await refreshed(client, sender)
    roles = {r["role"]: r for r in send["snapshot"]["roles"]}
    started = await client.post(f"{API}/label-runs", json=body(roles["ood_eval"]["version_id"]))
    run = millm.run_until_done(started.json()["id"])
    assert run.state == "completed", run.error
    before = (await client.post(f"{API}/detector-sets/{set_id}/checks")).json()
    d4 = next(c for c in before["outcomes"] if c["code"] == "D-4")
    assert d4["outcome"] == "green", d4
    with sync_session_factory()() as db:
        _, rev = db_factories.recipe(
            db,
            {
                "format": "midataworks.recipe/v1",
                "steps": [
                    {
                        "operator": "hard_negative_miner",
                        "version": "1",
                        "params": {"probe_label_run_id": run.id, "mode": "near"},
                    }
                ],
            },
        )
        train = db.get(Version, roles["train"]["version_id"])
        assert train is not None
        db.execute(text("ALTER TABLE dw_versions DISABLE TRIGGER dw_versions_immutable"))
        train.recipe_hash = rev.recipe_hash
        db.commit()
        db.execute(text("ALTER TABLE dw_versions ENABLE TRIGGER dw_versions_immutable"))
        db.commit()
    after = (await client.post(f"{API}/detector-sets/{set_id}/checks")).json()
    d4 = next(c for c in after["outcomes"] if c["code"] == "D-4")
    assert d4["outcome"] == "refused" and "mined from" in d4["reason"], d4


async def test_a_reproduction_is_never_reused_when_the_revision_was_not_reported(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    millm: Labeling,
    sender: SendDriver,  # after millm: the publisher's send_task patch must be the last one
) -> None:
    """Without a reported revision, sameness of model cannot be shown (P-13): every run re-runs
    the gate (control P23's regression test)."""
    await refreshed(client, sender)
    millm.millm.resident["revision"] = None
    first = await client.post(f"{API}/label-runs", json=body(make_version(data_dir, rows(2))))
    assert first.json()["labeler_identity"]["model_revision"] == "not reported"
    assert millm.run_until_done(first.json()["id"]).state == "completed"
    before = millm.millm.probe_score_calls
    second = await client.post(
        f"{API}/label-runs", json=body(make_version(data_dir, rows(2, tag="again")))
    )
    done = millm.run_until_done(second.json()["id"])
    assert done.state == "completed", done.error
    assert "cached_from_run_id" not in done.endpoint_snapshot["reproduction"]
    assert millm.millm.probe_score_calls - before == 1 + 60 + 2  # preflight + gate + rows


async def test_the_probe_list_says_when_a_probe_does_not_fit_the_loaded_model(
    client: httpx.AsyncClient, operator_name: str, millm: Labeling
) -> None:
    millm.millm.probes["pr_other"] = probe_row("pr_other", hf_id="google/gemma-2-2b-it")
    items = {i["probe_id"]: i for i in (await client.get(f"{API}/labeling/probes")).json()["items"]}
    assert items[PROBE]["fits_resident_model"] is True
    assert items["pr_other"]["fits_resident_model"] is False
