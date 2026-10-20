"""Feature 004's routes through the live app (FTASKS 5.4, 12.1–12.5)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.core.database import get_sync_engine, sync_session_factory
from src.services.curation import label_columns
from tests.fixtures import humor_pool as hp
from tests.support import db_factories as f
from tests.support.curation_fixtures import StepFixture, StubRegistry, labeler_info, make_version

AGENT = {"X-Dataworks-Agent": "agent:helper"}
LEVEL_WRITES = [
    ("PUT", "/api/v1/datasets/{ds}/shortcut-level", {"margin_pp": 20, "reason": "r"}),
    ("DELETE", "/api/v1/datasets/{ds}/shortcut-level", {"reason": "r"}),
    ("PUT", "/api/v1/settings/shortcut-level", {"margin_pp": 20, "reason": "r"}),
]


def _levels() -> int:
    with get_sync_engine().connect() as conn:
        return int(conn.execute(text("SELECT count(*) FROM dw_shortcut_levels")).scalar_one())


@pytest.fixture
def dataset_id(clean_db: None) -> str:
    with sync_session_factory()() as db:
        ds = f.dataset(db)
        db.commit()
        return str(ds.id)


@pytest.fixture
def stub_labeler(monkeypatch: pytest.MonkeyPatch) -> None:
    reg = StubRegistry(
        {
            "threshold_labeler": labeler_info(
                "threshold_labeler", {"label": "metadata", "label_probability": "metadata"}
            )
        }
    )
    monkeypatch.setattr(label_columns, "_registry", lambda: reg)


@pytest.mark.parametrize(("method", "path", "body"), LEVEL_WRITES)
async def test_agent_cannot_write_level(
    client: httpx.AsyncClient,
    dataset_id: str,
    operator_name: str,
    method: str,
    path: str,
    body: dict[str, Any],
) -> None:
    before = _levels()
    response = await client.request(method, path.format(ds=dataset_id), json=body, headers=AGENT)
    assert response.status_code == 403, response.text
    error = response.json()["error"]
    assert error["code"] == "agent_forbidden" and "warning levels" in error["message"]
    assert _levels() == before


async def test_agents_may_read_levels(client: httpx.AsyncClient, dataset_id: str) -> None:
    for path in (
        f"/api/v1/datasets/{dataset_id}/shortcut-level",
        "/api/v1/settings/shortcut-level",
    ):
        assert (await client.get(path, headers=AGENT)).status_code == 200


async def test_level_writes_record_who_and_history(
    client: httpx.AsyncClient, dataset_id: str, operator_name: str
) -> None:
    glob = await client.get("/api/v1/settings/shortcut-level")
    assert glob.json()["margin_pp"] == 10 and glob.json()["source"] == "code_default"
    put = await client.put(
        f"/api/v1/datasets/{dataset_id}/shortcut-level",
        json={"margin_pp": 25, "reason": "format is the target here"},
    )
    assert put.status_code == 201, put.text
    view = put.json()
    assert view["effective_margin_pp"] == 25 and view["source"] == "dataset"
    assert view["history"][0]["set_by"] == operator_name
    assert view["history"][0]["origin"] == "operator"
    cleared = await client.request(
        "DELETE", f"/api/v1/datasets/{dataset_id}/shortcut-level", json={"reason": "done"}
    )
    assert cleared.status_code == 201 and cleared.json()["source"] == "code_default"
    g = await client.put("/api/v1/settings/shortcut-level", json={"margin_pp": 12, "reason": "x"})
    assert g.status_code == 201 and g.json()["source"] == "set" and g.json()["margin_pp"] == 12


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"margin_pp": 20}, "override_reason_required"),
        ({"margin_pp": 20, "reason": "   "}, "override_reason_required"),
        ({"margin_pp": 100, "reason": "r"}, "margin_out_of_range"),
        ({"margin_pp": -1, "reason": "r"}, "margin_out_of_range"),
    ],
)
async def test_level_refusals(
    client: httpx.AsyncClient, dataset_id: str, operator_name: str, body: dict, code: str
) -> None:
    response = await client.put(f"/api/v1/datasets/{dataset_id}/shortcut-level", json=body)
    assert response.status_code == 422 and response.json()["error"]["code"] == code


async def test_unknown_dataset_is_404(client: httpx.AsyncClient, operator_name: str) -> None:
    response = await client.get("/api/v1/datasets/not-a-uuid/shortcut-level")
    assert response.status_code == 404 and response.json()["error"]["code"] == "dataset_not_found"


async def test_audit_run_and_read(
    client: httpx.AsyncClient, data_dir: Path, operator_name: str, stub_labeler: None
) -> None:
    with sync_session_factory()() as db:
        v = make_version(db, hp.candidates(), hp.ROLES, steps=[StepFixture("threshold_labeler")])
    missing = await client.get(f"/api/v1/versions/{v.id}/shortcut-audit")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "audit_not_run"
    run = await client.post(f"/api/v1/versions/{v.id}/shortcut-audit", json={})
    assert run.status_code == 200, run.text
    assert run.json()["outcome"] == "inline"
    again = await client.post(f"/api/v1/versions/{v.id}/shortcut-audit", json={})
    assert again.json()["outcome"] == "existing"
    read = (await client.get(f"/api/v1/versions/{v.id}/shortcut-audit")).json()
    assert {w["column"] for w in read["warnings"]} == {"source_label", "format"}
    assert read["level"]["source"] == "code_default"
    cells = await client.get(
        f"/api/v1/versions/{v.id}/shortcut-audit/cells",
        params={"column": "format", "value": "joke", "label": "humorous"},
    )
    body = cells.json()
    assert cells.status_code == 200 and body["total"] == 30 and len(body["rows"]) == 30
    again_cells = await client.get(
        f"/api/v1/versions/{v.id}/shortcut-audit/cells",
        params={"column": "format", "value": "joke", "label": "humorous"},
    )
    assert again_cells.json()["rows"] == body["rows"]  # seeded


async def test_audit_refusals_and_paths(
    client: httpx.AsyncClient, data_dir: Path, operator_name: str, stub_labeler: None
) -> None:
    with sync_session_factory()() as db:
        v = make_version(db, hp.candidates(), hp.ROLES)
    no_label = await client.post(f"/api/v1/versions/{v.id}/shortcut-audit", json={})
    assert no_label.status_code == 422 and no_label.json()["error"]["code"] == "no_label_column"
    traversal = await client.get("/api/v1/versions/..%2F..%2Fetc/shortcut-audit")
    assert traversal.status_code == 404
    assert (
        await client.post(f"/api/v1/versions/{v.id}/shortcut-audit", json={"path": "/etc/passwd"})
    ).status_code == 422
    big = await client.get(
        f"/api/v1/versions/{v.id}/shortcut-audit/cells",
        params={"column": "format", "value": "joke", "label": "humorous", "limit": 1000},
    )
    assert big.status_code == 422


async def test_report_routes_run_read_and_refuse(
    client: httpx.AsyncClient, data_dir: Path, operator_name: str
) -> None:
    import pyarrow as pa

    table = hp.candidates().slice(0, 300)
    splits = ["test" if i % 10 == 0 else "train" for i in range(table.num_rows)]
    table = table.set_column(
        table.schema.get_field_index("_dw_split"), "_dw_split", pa.array(splits)
    )
    with sync_session_factory()() as db:
        v = make_version(db, table, hp.ROLES, split_roles={"test": True})
    base = f"/api/v1/versions/{v.id}"
    for path, code in (
        ("profile", "profile_not_run"),
        ("leakage", "leakage_not_run"),
        ("contamination", "contamination_not_run"),
    ):
        missing = await client.get(f"{base}/{path}")
        assert missing.status_code == 404 and missing.json()["error"]["code"] == code
    run = await client.post(f"{base}/profile", json={"sample_size": 100})
    assert run.status_code == 200 and run.json()["outcome"] == "inline"
    assert (await client.get(f"{base}/profile")).json()["result"]["sample"] is True
    leak = await client.post(f"{base}/leakage", json={})
    assert leak.status_code == 200, leak.text
    assert (await client.get(f"{base}/leakage")).json()["result"]["sides"] == ["test", "train"]
    pairs = await client.get(f"{base}/leakage/pairs", params={"limit": 10})
    assert pairs.status_code == 200 and "pairs" in pairs.json()
    bad = await client.post(f"{base}/contamination", json={"benchmark_source_ids": [str(v.id)]})
    assert bad.status_code == 404 and bad.json()["error"]["code"] == "benchmark_not_found"
    trl = await client.post(f"{base}/trl-validation", json={"target_type": "nope"})
    assert trl.status_code == 422 and trl.json()["error"]["code"] == "unknown_target_type"
    sft = await client.post(f"{base}/trl-validation", json={"target_type": "sft"})
    assert sft.status_code == 200 and sft.json()["valid"] is True
    benches = (await client.get("/api/v1/curation/benchmarks")).json()["items"]
    assert benches[0]["repo_id"] == "tasksource/humicroedit"


async def test_large_inputs_start_a_job(
    client: httpx.AsyncClient, data_dir: Path, operator_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.core.config import get_settings
    from src.services import job_service

    monkeypatch.setattr(get_settings(), "curation_inline_max_rows", 10)
    sent: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        job_service, "dispatch_queued", lambda db, **_: sent.append(("dispatch", db)) or []
    )
    with sync_session_factory()() as db:
        v = make_version(db, hp.candidates().slice(0, 50), hp.ROLES)
    first = await client.post(f"/api/v1/versions/{v.id}/profile", json={})
    assert first.status_code == 202 and first.json()["outcome"] == "started"
    again = await client.post(f"/api/v1/versions/{v.id}/profile", json={})
    assert again.status_code == 202 and again.json()["outcome"] == "running"
    assert again.json()["job_id"] == first.json()["job_id"]


def test_every_curation_code_has_a_status_and_renders_the_envelope() -> None:
    """FTASKS 12.4: each code maps to a real status (none falls through to 500) and the error
    renders as the one envelope through Foundation's handler."""
    import asyncio
    import json

    from src.core.errors import AppError
    from src.main import fastapi_app
    from src.services.curation.errors import STATUS_FOR_CODE, CurationError

    handler = fastapi_app.exception_handlers[AppError]
    for code, status in STATUS_FOR_CODE.items():
        error = CurationError(code, f"what to do next for {code}", {"k": 1})
        assert error.status_code == status != 500, code
        response = asyncio.run(handler(None, error))  # type: ignore[arg-type]
        body = json.loads(response.body)
        assert response.status_code == status
        assert body == {
            "error": {"code": code, "message": f"what to do next for {code}", "details": {"k": 1}}
        }
