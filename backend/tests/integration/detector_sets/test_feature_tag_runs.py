"""Feature-tag label runs and the feature filter (009 FTASKS 16.1 - 16.4; FR-009.65 - FR-009.68,
FR-009.74). A feature-tag run is 005's ``millm_sae_features`` protocol (the operator decision of
2026-10-07 applied to the tagger by the same reasoning); the filter is a 003 operator reading the
finished run. miLLM is the fake, serving ``return_sae_activations`` in scoring mode with miLLM
44e4c4a's response block and refusals."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pytest
from sqlalchemy import select

from src.core.config import get_settings
from src.core.database import sync_session_factory
from src.models.label import Label
from tests.support.fake_millm import ORIGIN
from tests.support.labeling_fixtures import Labeling, labeling, make_version, row_key  # noqa: F401

API = "/api/v1"
LLAMA = {
    "id": 3,
    "name": "Llama-3.1-8B-Instruct",
    "repo_id": "meta-llama/Llama-3.1-8B-Instruct",
    "revision": "0e9e39f249a16976918f6564b8830bc894c89659",
    "quantization": "FP16",
}


@pytest.fixture
def millm(labeling: Labeling, monkeypatch: pytest.MonkeyPatch) -> Labeling:  # noqa: F811
    labeling.millm.resident = dict(LLAMA)
    labeling.millm.attachments = [{"sae_id": "sae_l16", "layer": 16}]
    monkeypatch.setattr(get_settings(), "millm_base_url", ORIGIN)
    return labeling


def body(version_id: str, **over: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "input_version_id": version_id,
        "role": "features",
        "features": {"top_k": 4, "positions": "last"},
        "field_map": {"text": "text"},
    }
    out.update(over)
    return out


def labels_of(run_id: str) -> dict[str, Label]:
    with sync_session_factory()() as db:
        found = db.execute(select(Label).where(Label.label_run_id == run_id)).scalars().all()
        for label in found:
            db.expunge(label)
        return {label.row_key: label for label in found}


TEXTS = [f"headline {i} about cheese" for i in range(5)]


async def test_a_feature_tag_run_records_each_rows_top_features_unsteered(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, millm: Labeling
) -> None:
    version_id = make_version(data_dir, TEXTS)
    planned = await client.get(
        f"{API}/label-runs/plan", params={"request": json.dumps(body(version_id))}
    )
    assert planned.status_code == 200, planned.text
    plan = planned.json()
    assert plan["features"]["sae_id"] == "sae_l16" and plan["features"]["layer"] == 16
    assert plan["features"]["preflight"]["read_point"] == "unsteered"
    assert plan["labeler_identity"]["read_point"] == "unsteered"
    assert plan["labeler_identity"]["protocol"] == "millm_sae_features"
    started = await client.post(f"{API}/label-runs", json=body(version_id))
    assert started.status_code == 201, started.text
    assert started.json()["kind"] == "feature_tag"
    final = millm.run_until_done(started.json()["id"])
    assert final.state == "completed", final.error
    assert final.counts == {"tagged": 5} and final.pinned is True
    calls = [c for c in millm.millm.calls("/v1/completions", "POST")
             if "x-millm-lease" in c.headers]  # fmt: skip
    assert len(calls) == 5  # one request per row, each under the lease
    for call in calls:
        assert call.body["max_tokens"] == 1 and call.body["logprobs"] == 1
        assert call.body["return_sae_activations"] == {
            "sae_id": "sae_l16", "top_k": 4, "positions": "last"
        }  # fmt: skip
        assert call.headers["x-millm-load-policy"] == "refuse"
    tag = labels_of(final.id)[row_key(TEXTS[0])]
    assert tag.outcome == "tagged" and tag.parsed_value["read_point"] == "unsteered"
    [position] = tag.parsed_value["positions"]
    assert len(position["features"]) == 4

    # The filter keeps rows whose named feature reached the cutoff, from the finished run only.
    from src.operators.context import RunContext
    from src.operators.manifest import manifest_hash
    from src.operators.native.detector.feature_filter import MANIFEST, FeatureFilter

    feature = position["features"][0]["index"]
    value = position["features"][0]["value"]
    batch = pa.table(
        {"_dw_row_key": [row_key(t) for t in TEXTS], "_dw_occurrence": [0] * 5, "text": TEXTS}
    )
    ctx = RunContext(
        MANIFEST, manifest_hash(MANIFEST), 1, None, {"text": "content"}, "dw.rowkey/v1",
        bindings=[{"kind": "label_run", "id": final.id}],
    )  # fmt: skip
    params = {"feature_tag_run_id": final.id, "features": [feature], "min_activation": value}
    out = FeatureFilter().run(batch, params, ctx)
    assert row_key(TEXTS[0]) in out.output.column("_dw_row_key").to_pylist()
    for event in out.events:
        assert event.statistic_name == "activation"
        assert event.reason_code in ("below_activation", "activation_unknown")
    stats = FeatureFilter().compute_statistics(batch, params, ctx)["activation"].to_pylist()
    assert stats[0] == value


async def test_a_read_point_other_than_unsteered_stops_the_run(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, millm: Labeling
) -> None:
    version_id = make_version(data_dir, TEXTS)
    started = await client.post(f"{API}/label-runs", json=body(version_id))
    assert started.status_code == 201, started.text
    millm.millm.sae_read_point = "post_steering"
    final = millm.run_until_done(started.json()["id"])
    assert final.state == "failed" and final.error["code"] == "READ_POINT_MISMATCH"
    assert labels_of(final.id) == {}


@pytest.mark.parametrize(
    ("arrange", "over", "code", "said"),
    [
        (lambda m: m.attachments.clear(), {}, "SAE_NOT_ATTACHED", "attached: none"),
        (lambda m: None, {"features": {"top_k": 999, "positions": "last"}},
         "SAE_ACTIVATIONS_REFUSED", "exceeds the limit"),
        (lambda m: setattr(m, "engine", "llama.cpp"), {}, "SAE_GGUF_UNSUPPORTED", "llama.cpp"),
        (lambda m: None, {"features": {"sae_id": "sae_x", "top_k": 4, "positions": "last"}},
         "SAE_NOT_ATTACHED", "sae_x"),
    ],
)  # fmt: skip
async def test_the_plan_refuses_with_millms_reason(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    millm: Labeling,
    arrange: Any,
    over: dict[str, Any],
    code: str,
    said: str,
) -> None:
    arrange(millm.millm)
    version_id = make_version(data_dir, TEXTS)
    response = await client.get(
        f"{API}/label-runs/plan", params={"request": json.dumps(body(version_id, **over))}
    )
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == code and said in error["message"], error


async def test_agent_tagging_counts_as_feature_tagging_and_waits_over_the_threshold(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    millm: Labeling,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.models.agent_label_row import AgentLabelRow

    monkeypatch.setattr(get_settings(), "agent_label_row_threshold", 6)
    agent = {"X-Dataworks-Agent": "agent:claude"}
    version_id = make_version(data_dir, TEXTS)
    first = await client.post(f"{API}/label-runs", json=body(version_id), headers=agent)
    assert first.status_code == 201, first.text
    with sync_session_factory()() as db:
        [row] = db.execute(select(AgentLabelRow)).scalars().all()
        assert (row.run_kind, row.rows_counted) == ("feature_tagging", 5)
    second = await client.post(f"{API}/label-runs", json=body(version_id), headers=agent)
    assert second.status_code == 202, second.text  # 5 + 5 summed on the version > 6
    assert second.json()["action"] == "agent_label_rows"
