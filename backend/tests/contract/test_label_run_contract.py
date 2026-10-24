"""The label-run contract read through public routes, as 006, 008 and 009 will (005 FTASKS 13.8;
FPRD 005 section 7.4)."""

from __future__ import annotations

import httpx

from tests.integration.labeling.helpers import setup_classifier, start_body
from tests.support.labeling_fixtures import Labeling, labeling

__all__ = ["labeling"]

RUN_FIELDS = {
    "id",
    "kind",
    "state",
    "input_version_id",
    "endpoint_snapshot",
    "template_ref",
    "question",
    "threshold_positive",
    "threshold_negative",
    "label_set",
    "sampling",
    "structured_output",
    "packing",
    "chunk_size",
    "parent_run_ids",
    "labeler_identity",
    "labeler_identity_hash",
    "labeler_fingerprint",
    "pinned",
    "revision_reported",
    "counts",
    "keep_share_estimate",
    "keep_share_actual",
    "rows_total",
    "rows_reused",
    "started_by",
    "started_by_origin",
    "room",
}
LABEL_FIELDS = {
    "label_run_id",
    "row_key",
    "labeler_fingerprint",
    "outcome",
    "parsed_value",
    "probability",
    "distribution",
    "raw_output",
    "rationale",
    "steering_state",
    "latency_ms",
    "skip_reason",
    "provisional",
    "reused_from_run_id",
    "started_by",
    "started_by_origin",
}
OUTCOMES = {"positive", "negative", "excluded", "skipped", "parse_failure", "position_inconsistent"}


async def test_a_completed_run_reads_back_every_contract_field(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    version_id, template_id = await setup_classifier(client, labeling, n=12)
    started = (
        await client.post("/api/v1/label-runs", json=start_body(version_id, template_id))
    ).json()
    labeling.run_until_done(started["id"])
    run = (await client.get(f"/api/v1/label-runs/{started['id']}")).json()
    assert RUN_FIELDS <= set(run)
    assert run["kind"] == "classifier" and run["state"] == "completed"
    assert run["room"] == f"dataworks/label-runs/{run['id']}"
    assert run["pinned"] is True and run["revision_reported"] is True
    assert run["started_by"] and run["started_by_origin"] in {"operator", "agent"}
    identity = run["labeler_identity"]
    assert set(identity) == {"protocol", "model_id", "model_revision", "template", "question"}
    snapshot = run["endpoint_snapshot"]
    assert {"role", "protocol", "base_url", "model_id", "model_revision", "server_kind"} <= set(
        snapshot
    )
    assert "api_key" not in snapshot
    page = (await client.get(f"/api/v1/label-runs/{run['id']}/labels")).json()
    assert page["total"] == 12
    for label in page["items"]:
        assert LABEL_FIELDS <= set(label)
        assert label["outcome"] in OUTCOMES
        assert label["provisional"] is False
        assert label["started_by"] == run["started_by"]
        assert label["labeler_fingerprint"] == run["labeler_fingerprint"]
    listing = (await client.get(f"/api/v1/label-runs?input_version_id={version_id}")).json()
    assert [r["id"] for r in listing["items"]] == [run["id"]]
    bound = await client.get(f"/api/v1/label-runs/{run['id']}/labels?outcome=positive&limit=500")
    assert bound.status_code == 200
    assert (await client.get(f"/api/v1/label-runs/{run['id']}/labels?limit=501")).status_code == 422
