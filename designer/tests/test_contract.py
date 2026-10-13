"""Every offered Data Designer operator, through the REAL library and the loopback relay, against a
fake OpenAI-compatible server (FR-003.15; FTASKS 8.6). Asserts payload, headers and call count.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

import dd_paths
from src.operators.data_designer import runner
from tests.support.fake_openai import FakeOpenAI

CATALOGUE = json.loads(dd_paths.CATALOGUE.read_text())
KEY = "sk-designer-NeverWriteMe-0123"


def _entry(name: str) -> dict[str, Any]:
    return next(o for o in CATALOGUE["operators"] if o["manifest"]["name"] == name)


def _payload(name: str, params: dict[str, Any], endpoint: dict[str, Any] | None) -> dict[str, Any]:
    entry = _entry(name)
    default = entry["manifest"]["params_schema"]["properties"]["output_column"]["default"]
    return {
        "operator": f"{name}@x",
        "column_class": entry["column_class"],
        "column_fields": entry["column_fields"],
        "params": params,
        "params_schema": entry["manifest"]["params_schema"],
        "output_column": params.get("output_column", default),
        "seed_columns": ["text"],
        "endpoint": endpoint,
        "key_ref": None,
        "lease_id": "lease-7",
        "body_overrides": {"profile": "p1"},
    }


def _table(texts: list[str]) -> pa.Table:
    return pa.table(
        {
            "text": texts,
            "_dw_row_key": [f"{i:064x}" for i in range(len(texts))],
            "_dw_occurrence": pa.array([0] * len(texts), pa.int32()),
        }
    )


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return tmp_path


def test_the_catalogue_offers_what_can_be_configured() -> None:
    offered = {o["manifest"]["name"] for o in CATALOGUE["operators"]}
    rejected = {r["name"] for r in CATALOGUE["rejected"]}
    assert offered == {"dd_llm_text", "dd_expression"}
    assert rejected == {"dd_llm_judge", "dd_llm_structured", "dd_validation"}


def test_llm_text_through_the_relay_payload_headers_and_count() -> None:
    texts = ["first row", "please OVERFLOW here", "third row"]
    with FakeOpenAI() as upstream:
        endpoint = {"base_url": upstream.base_url, "model": "m1", "is_millm": True}
        payload = _payload("dd_llm_text", {"prompt": "Rewrite: {{ text }}"}, endpoint)
        produced, records = runner.generate(_table(texts), payload, KEY)
        requests = list(upstream.requests)
    assert len(requests) == 3, "one request per row, no retries of a non-retryable failure"
    for sent in requests:
        assert sent["headers"]["authorization"] == f"Bearer {KEY}"
        assert sent["headers"]["x-millm-strict"] == "true"
        assert sent["headers"]["x-millm-load-policy"] == "refuse"
        assert sent["headers"]["x-millm-lease"] == "lease-7"
        assert sent["body"]["profile"] == "p1" and sent["body"]["model"] == "m1"
    assert sorted(r["_dw_row_key"] for r in produced) == [f"{0:064x}", f"{2:064x}"]
    assert all(r["llm_text"].startswith("echo: Rewrite:") for r in produced)
    assert len(records) == 3
    assert [r["reason"] for r in records].count("context_overflow") == 1
    assert {r["steering_header"] for r in records if r["status"] == 200} == {"profile=p1;strength=0.4"}
    assert "DW_RELAY_NONCE" not in os.environ
    assert KEY not in json.dumps(records)


def test_expression_needs_no_endpoint() -> None:
    payload = _payload("dd_expression", {"expr": "{{ text | upper }}", "dtype": "str"}, None)
    produced, records = runner.generate(_table(["abc", "de"]), payload, None)
    assert [r["expression"] for r in produced] == ["ABC", "DE"] and records == []


def test_a_step_writes_output_and_records_and_a_bad_param_writes_error_json(
    data_dir: Path,
) -> None:
    import pyarrow.parquet as pq

    (data_dir / "in").mkdir()
    pq.write_table(_table(["abc", "de"]), data_dir / "in" / "part-00000.parquet")
    payload = {
        **_payload("dd_expression", {"expr": "{{ text | upper }}", "dtype": "str"}, None),
        "input_dir": "in",
        "output_dir": "runs/j/s.dd",
    }
    assert runner.run_step(payload)["produced"] == 2
    out = data_dir / "runs" / "j" / "s.dd"
    assert pq.read_table(out / "output.parquet").column("expression").to_pylist() == ["ABC", "DE"]
    assert json.loads((out / "records.json").read_text()) == []
    bad = {**payload, "params": {"expr": 5}, "output_dir": "runs/j/bad.dd"}
    with pytest.raises(ValueError):
        runner.run_step(bad)
    error = json.loads((data_dir / "runs" / "j" / "bad.dd" / "error.json").read_text())
    assert error["code"] == "params_invalid"
