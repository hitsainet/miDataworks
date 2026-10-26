"""The detector-set results tools carry feature 011's fields (FR-011.8): each role's
``commit_check`` raw and as a phrase, ``run_discovery``, and a paired score's ``source`` and
``skipped``. The tools pass the REST body through; the body here is the REAL ``results_out`` of a
snapshot, so a field the REST side drops is missing here too."""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.models.detector_results import DetectorResults
from src.services.detector_sets import commit_check, results_service
from tests.support.mcp_harness import build_harness, call_tool


def snapshot(provenance: dict[str, Any] | None) -> dict[str, Any]:
    row = DetectorResults(
        id="dres_1",
        set_id="dts_1",
        send_id="dsn_1",
        mistudio_base_url="http://mistudio.test",
        read_by="operator",
        read_by_origin="operator",
        runs=[],
        figures=[
            {
                "probe_id": "pm_1",
                "reward": False,
                "sets": [
                    {
                        "probe_dataset_id": "pmd_ood",
                        "evaluation_id": "pme_1",
                        "paired": {
                            "available": True,
                            "source": "mistudio",
                            "skipped": [],
                        },
                    }
                ],
            }
        ],
        report_sha256={},
        gone={"runs": [], "probes": []},
        provenance=provenance,
    )
    return results_service.results_out(row)


def text_of(result: Any) -> dict[str, Any]:
    content = result[0] if isinstance(result, tuple) else result
    if isinstance(content, dict):
        return content
    if isinstance(content, list):
        content = content[0]
    data: dict[str, Any] = json.loads(content.text)
    return data


@pytest.mark.parametrize("value", ["verified", "claimed", None])
def test_get_probe_results_carries_the_commit_check_and_its_phrase(value: str | None) -> None:
    body = snapshot(
        {
            "run_discovery": {"method": "filter", "limit": 200},
            "commits": [
                {
                    "role": "ood_eval",
                    "commit": "c" * 40,
                    "commit_check": value,
                    "commit_check_phrase": commit_check.phrase_of(value),
                }
            ],
        }
    )
    mcp, client = build_harness(response=body)
    got = text_of(call_tool(mcp, "dataworks_get_probe_results", {"set_id": "dts_1"}))
    assert client.calls == [("GET", "/detector-sets/dts_1/results", {"snapshot": "latest"})]
    [row] = got["commits"]
    assert row["commit_check"] == value
    assert (
        row["commit_check_phrase"]
        == {
            "verified": "verified by miStudio",
            "claimed": "claimed (miStudio does not pin downloads)",
            None: "not checked (sent before feature 011)",
        }[value]
    )
    assert got["run_discovery"] == {"method": "filter", "limit": 200}
    paired = got["evaluations"][0]["sets"][0]["paired"]
    assert paired["source"] == "mistudio" and paired["skipped"] == []


def test_a_pre_011_snapshot_reads_not_recorded_through_mcp() -> None:
    mcp, client = build_harness(response=snapshot(None))
    got = text_of(call_tool(mcp, "dataworks_refresh_probe_results", {"set_id": "dts_1"}))
    assert client.calls == [("POST", "/detector-sets/dts_1/results/refresh", {"json_body": None})]
    assert got["commits"] is None and got["run_discovery"] is None
