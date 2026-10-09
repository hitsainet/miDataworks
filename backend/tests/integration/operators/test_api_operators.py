"""The operator REST surface, through the live app (FR-003.24; FTASKS 9.2, 9.5–9.7, 10.2–10.6, 5.7).

Every route is called with its payload and status asserted. Previews run the REAL preview task body
in-process (``send_preview`` replaced), so the route, the Redis hand-off and the worker code all run.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.api.v1.endpoints import operators as endpoint
from src.core.config import get_settings
from src.core.database import Base, get_sync_engine
from src.operators import preview
from src.operators import registry as registry_module
from src.operators.native.fixtures import FIXTURE_OPERATORS
from src.operators.registry import OperatorRegistry
from src.services import operator_port
from tests.integration.test_version_build import request, setup, src
from tests.support.operator_build_driver import RealDriver, real_driver
from tests.support.plugin_dist import make_distribution
from tests.support.stub_operators import body
from tests.support.version_fixtures import HUMOR_TRAIN, make_source

__all__ = ["real_driver"]
OPS = "/api/v1/operators"
AGENT = {"X-Dataworks-Agent": "agent:helper"}


@pytest.fixture
def inline_previews(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Run each queued preview in-process, through the real task body."""
    sent: list[dict[str, Any]] = []

    def run(req: dict[str, Any], entry: Any) -> None:
        sent.append(req)
        preview.run_request(req)

    monkeypatch.setattr(endpoint, "send_preview", run)
    return sent


@pytest.fixture
async def version_id(
    client: httpx.AsyncClient, real_driver: RealDriver, data_dir: Path, operator_name: str
) -> str:
    source = make_source(data_dir, {"train": HUMOR_TRAIN})
    ds, rev = await setup(client, body(("fx_keep_all", {})))
    job = (await request(client, ds, rev, [src(source)], seed=1)).json()["job_id"]
    assert real_driver.run(job) == "completed"
    with get_sync_engine().connect() as conn:
        vid = conn.execute(text("SELECT id FROM dw_versions")).scalar_one()
    return str(vid)


def _counts() -> dict[str, int]:
    with get_sync_engine().connect() as conn:
        return {
            t.name: int(conn.execute(text(f"SELECT count(*) FROM {t.name}")).scalar_one())
            for t in Base.metadata.sorted_tables
            if not t.name.startswith("dw_row_events_p")
        }


# --- catalogue -------------------------------------------------------------------------------


async def test_catalogue_lists_the_live_registry_and_filters(
    client: httpx.AsyncClient, real_driver: RealDriver
) -> None:
    response = await client.get(OPS, params={"limit": 200})
    assert response.status_code == 200
    data = response.json()
    names = {i["name"] for i in data["items"]}
    assert {c.manifest.name for c in FIXTURE_OPERATORS} <= names
    assert data["summary"]["allowed"] >= len(FIXTURE_OPERATORS)
    filters = (await client.get(OPS, params={"kind": "filter", "provider": "native"})).json()
    assert filters["items"] and all(i["kind"] == "filter" for i in filters["items"])
    labeling = (await client.get(OPS, params={"kind": "labeler"})).json()["items"]
    assert [i["name"] for i in labeling] == ["fx_endpoint_probe", "fx_relay_echo"]


async def test_removing_a_native_entry_removes_it_from_the_route(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """4.7: the route reads the ONE native list through the registry build."""
    from src.operators.native import fixtures

    monkeypatch.setattr(operator_port, "_registry", operator_port.NoOperatorsInstalled())
    monkeypatch.setattr(registry_module, "_process", None)
    shorter = tuple(c for c in fixtures.FIXTURE_OPERATORS if c is not fixtures.KeepAll)
    monkeypatch.setattr(fixtures, "FIXTURE_OPERATORS", shorter)
    names = {i["name"] for i in (await client.get(OPS, params={"limit": 200})).json()["items"]}
    assert "fx_keep_all" not in names and "fx_drop_short" in names


async def test_manifest_versions_and_not_found(
    client: httpx.AsyncClient, real_driver: RealDriver
) -> None:
    one = await client.get(f"{OPS}/fx_drop_short/1")
    assert one.status_code == 200
    assert one.json()["manifest"]["thresholds"][0]["param"] == "min_len"
    assert one.json()["state"] == "allowed" and len(one.json()["manifest_hash"]) == 64
    versions = await client.get(f"{OPS}/fx_drop_short")
    assert versions.json()["current_version"] == "1"
    missing = await client.get(f"{OPS}/ghost/1")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "operator_not_found"
    moved = await client.get(f"{OPS}/fx_drop_short/9")
    assert moved.status_code == 409 and moved.json()["error"]["code"] == "version_unavailable"
    assert (await client.get(f"{OPS}/ghost")).status_code == 404


async def test_schema_subset_is_published(client: httpx.AsyncClient) -> None:
    data = (await client.get(f"{OPS}/schema-subset")).json()
    assert "$ref" in data["refused"] and "minimum" in data["by_type"]["integer"]


# --- validation --------------------------------------------------------------------------------


async def test_min_len_abc_is_422_with_its_pointer(
    client: httpx.AsyncClient, real_driver: RealDriver
) -> None:
    """FR-003.7 at the API: 422, per-field, nothing queued."""
    bad = await client.post(f"{OPS}/fx_drop_short/1/validate", json={"params": {"min_len": "abc"}})
    assert bad.status_code == 422
    error = bad.json()["error"]
    assert error["code"] == "params_invalid"
    assert error["details"]["errors"] == [
        {"pointer": "/min_len", "message": "'abc' is not of type 'integer'"}
    ]
    ok = await client.post(f"{OPS}/fx_drop_short/1/validate", json={"params": {"min_len": 3}})
    assert ok.status_code == 200 and ok.json() == {"valid": True}


async def test_unknown_request_fields_are_refused(client: httpx.AsyncClient) -> None:
    response = await client.post(f"{OPS}/fx_drop_short/1/validate", json={"params": {}, "x": 1})
    assert response.status_code == 422


# --- previews ---------------------------------------------------------------------------------


async def test_preview_counts_examples_and_writes_nothing(
    client: httpx.AsyncClient,
    version_id: str,
    inline_previews: list[dict[str, Any]],
    data_dir: Path,
) -> None:
    before = _counts()
    paths_before = set(data_dir.rglob("*"))
    response = await client.post(
        f"{OPS}/fx_drop_short/1/preview",
        json={"params": {"min_len": 25}, "input": {"version_id": version_id}, "seed": 4},
    )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["sample_size"] == 10 and result["counts"]["in"] == 10
    assert result["counts"]["dropped"] == result["drops_total"] > 0
    dropped = result["examples"]["dropped"][0]
    assert dropped["reason_code"] == "too_short" and dropped["statistic_name"] == "text_length"
    assert len(inline_previews) == 1
    assert _counts() == before, "a preview writes no row anywhere"
    assert set(data_dir.rglob("*")) == paths_before, "a preview creates no file or directory"


async def test_identical_previews_share_one_run(
    client: httpx.AsyncClient, version_id: str, inline_previews: list[dict[str, Any]]
) -> None:
    payload = {"params": {"min_len": 11}, "input": {"version_id": version_id}, "seed": 9}
    first = await client.post(f"{OPS}/fx_drop_short/1/preview", json=payload)
    second = await client.post(f"{OPS}/fx_drop_short/1/preview", json=payload)
    assert first.json() == second.json()
    assert len(inline_previews) == 1


async def test_a_slow_preview_answers_202_then_200(
    client: httpx.AsyncClient, version_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """9.6: 202 after the sync wait, then the poll returns the result."""
    monkeypatch.setattr(get_settings(), "operator_preview_sync_wait_s", 0.2)

    def later(req: dict[str, Any], entry: Any) -> None:
        threading.Thread(
            target=lambda: (time.sleep(0.6), preview.run_request(req)), daemon=True
        ).start()

    monkeypatch.setattr(endpoint, "send_preview", later)
    response = await client.post(
        f"{OPS}/fx_slow/1/preview",
        json={"params": {"seconds": 0}, "input": {"version_id": version_id}},
    )
    assert response.status_code == 202
    preview_id = response.json()["preview_id"]
    running = (await client.get(f"{OPS}/previews/{preview_id}")).json()
    assert running["status"] == "running"
    for _ in range(50):
        polled = (await client.get(f"{OPS}/previews/{preview_id}")).json()
        if polled["status"] == "done":
            break
        time.sleep(0.1)
    assert polled["status"] == "done" and polled["result"]["counts"]["kept"] == 10


async def test_unknown_preview_is_404(client: httpx.AsyncClient) -> None:
    response = await client.get(f"{OPS}/previews/{'0' * 64}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "preview_not_found"


async def test_preview_timeout_is_reported(
    client: httpx.AsyncClient,
    version_id: str,
    inline_previews: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "operator_preview_soft_limit_s", 0.05)
    response = await client.post(
        f"{OPS}/fx_slow/1/preview",
        json={"params": {"seconds": 1}, "input": {"version_id": version_id}},
    )
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "preview_timeout"


async def test_statistics_return_every_value_and_flag_a_constant(
    client: httpx.AsyncClient, version_id: str, inline_previews: list[dict[str, Any]]
) -> None:
    response = await client.post(
        f"{OPS}/fx_drop_short/1/statistics",
        json={"params": {"min_len": 10}, "input": {"version_id": version_id}},
    )
    assert response.status_code == 200, response.text
    threshold = response.json()["result"]["thresholds"][0]
    assert threshold["statistic"] == "text_length" and threshold["unit"] == "characters"
    assert len(threshold["values"]) == 10 and threshold["constant"] is False
    assert {v["value"] for v in threshold["values"]} >= {5.0}
    none = await client.post(
        f"{OPS}/fx_keep_all/1/statistics", json={"params": {}, "input": {"version_id": version_id}}
    )
    assert none.status_code == 409 and none.json()["error"]["code"] == "no_threshold"


async def test_constant_statistic_and_empty_sample(
    client: httpx.AsyncClient,
    real_driver: RealDriver,
    data_dir: Path,
    operator_name: str,
    inline_previews: list[dict[str, Any]],
) -> None:
    """9.5: one length everywhere is flagged (the empty sample is in unit/test_preview.py)."""
    same = [{"text": "abcde", "label": i % 2, "score": 0.1, "note": str(i)} for i in range(4)]
    source2 = make_source(data_dir, {"train": same}, repo_id="org/same2")
    ds2, rev2 = await setup(client, body(("fx_keep_all", {})), name="same2")
    job2 = (await request(client, ds2, rev2, [src(source2)], seed=1)).json()["job_id"]
    real_driver.run(job2)
    with get_sync_engine().connect() as conn:
        vid2 = str(
            conn.execute(
                text("SELECT id FROM dw_versions WHERE dataset_id = :d"), {"d": ds2}
            ).scalar_one()
        )
    stats = await client.post(
        f"{OPS}/fx_drop_short/1/statistics",
        json={"params": {"min_len": 3}, "input": {"version_id": vid2}},
    )
    assert stats.json()["result"]["thresholds"][0]["constant"] is True


async def test_sample_size_cap_is_enforced(
    client: httpx.AsyncClient, version_id: str, inline_previews: list[dict[str, Any]]
) -> None:
    response = await client.post(
        f"{OPS}/fx_drop_short/1/preview",
        json={"params": {"min_len": 3}, "input": {"version_id": version_id}, "sample_size": 5000},
    )
    assert response.status_code == 422
    assert inline_previews == []


# --- upgrade plan --------------------------------------------------------------------------------


async def test_upgrade_plan_reports_per_step_and_never_fills_params(
    client: httpx.AsyncClient, real_driver: RealDriver
) -> None:
    """9.7."""
    plan = await client.post(
        f"{OPS}/upgrade-plan",
        json={
            "recipe_body": {
                "format": "dw.recipe/v1",
                "steps": [
                    {"operator": "fx_drop_short", "version": "0", "params": {}},
                    {"operator": "fx_keep_all", "version": "1", "params": {}},
                    {"operator": "ghost", "version": "1", "params": {}},
                ],
            }
        },
    )
    assert plan.status_code == 200
    steps = plan.json()["steps"]
    assert steps[0]["from"] == "0" and steps[0]["to"] == "1" and steps[0]["changes"]
    assert steps[0]["params_valid"] is False and steps[0]["errors"][0]["pointer"] == ""
    assert steps[1]["params_valid"] is True and steps[1]["changes"] is False
    assert steps[2]["to"] is None and steps[2]["params_valid"] is False


# --- the allowlist ---------------------------------------------------------------------------


@pytest.fixture
def plugin_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, real_driver: RealDriver
) -> OperatorRegistry:
    make_distribution(tmp_path / "plugin", monkeypatch)
    reg = OperatorRegistry.build(native=FIXTURE_OPERATORS, catalogues=())
    monkeypatch.setattr(operator_port, "_registry", reg)
    monkeypatch.setattr(registry_module, "_process", reg)
    real_driver.registry = reg
    return reg


ENTRY = {"distribution": "acme-ops", "distribution_version": "1.0", "entry_point": "tagger"}


async def test_an_agent_cannot_change_the_allowlist(
    client: httpx.AsyncClient, plugin_installed: OperatorRegistry, operator_name: str
) -> None:
    """10.4 / P-09: 403, nothing written; the agent can still read."""
    for path in (f"{OPS}/allowlist", f"{OPS}/allowlist/revoke"):
        response = await client.post(path, json={**ENTRY, "reason": "x"}, headers=AGENT)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "agent_forbidden"
    with get_sync_engine().connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM dw_operator_allowlist")).scalar_one() == 0
    read = await client.get(f"{OPS}/allowlist", headers=AGENT)
    assert read.status_code == 200 and read.json()["items"][0]["state"] == "not_allowed"


async def test_allow_run_revoke_refuse(
    client: httpx.AsyncClient,
    plugin_installed: OperatorRegistry,
    real_driver: RealDriver,
    data_dir: Path,
    operator_name: str,
    inline_previews: list[dict[str, Any]],
) -> None:
    """5.7 and 10.5: allow via REST, run a step, revoke, the next step and preview refuse."""
    blocked = await client.get(OPS, params={"state": "not_allowed"})
    assert [i["name"] for i in blocked.json()["items"]] == ["tagger"]
    allowed = await client.post(f"{OPS}/allowlist", json={**ENTRY, "reason": "reviewed the code"})
    assert allowed.status_code == 201, allowed.text
    assert allowed.json()["state"] == "allowed" and allowed.json()["changed_by"] == "Test Operator"
    assert allowed.json()["operators"] == ["acme_tagger@1"]
    source = make_source(data_dir, {"train": HUMOR_TRAIN})
    ds, rev = await setup(client, body(("acme_tagger", {})))
    job = (await request(client, ds, rev, [src(source)], seed=1)).json()["job_id"]
    assert real_driver.run(job) == "completed"
    with get_sync_engine().connect() as conn:
        vid = str(conn.execute(text("SELECT id FROM dw_versions")).scalar_one())
    revoked = await client.post(f"{OPS}/allowlist/revoke", json={**ENTRY, "reason": "pulled"})
    assert revoked.status_code == 201 and revoked.json()["state"] == "not_allowed"
    refused = await client.post(
        f"{OPS}/acme_tagger/1/preview", json={"params": {}, "input": {"version_id": vid}}
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "operator_not_allowed"
    assert inline_previews == [], "the API refuses before anything is queued (FR-003.12)"
    validate = await client.post(f"{OPS}/acme_tagger/1/validate", json={"params": {}})
    assert validate.status_code == 409
    recipe = await client.post("/api/v1/recipes/validate", json={"body": body(("acme_tagger", {}))})
    codes = [e["code"] for s in recipe.json()["steps"] for e in s["errors"]]
    assert "operator_not_allowed" in codes
    saved = await client.post(
        "/api/v1/recipes", json={"name": "again", "body": body(("acme_tagger", {}))}
    )
    assert saved.status_code == 422
    assert "operator_not_allowed" in saved.text
    history = (await client.get(f"{OPS}/allowlist")).json()["items"][0]["history"]
    assert [h["action"] for h in history] == ["revoke", "allow"]


async def test_allowing_an_uninstalled_entry_point_is_404(
    client: httpx.AsyncClient, plugin_installed: OperatorRegistry, operator_name: str
) -> None:
    response = await client.post(
        f"{OPS}/allowlist", json={**ENTRY, "distribution_version": "9.9", "reason": "x"}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "entry_point_not_installed"


async def test_min_len_abc_to_datajuicer_text_length_filter_is_422_and_creates_no_job(
    client: httpx.AsyncClient, operator_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-003.7's named regression on the real Data-Juicer catalogue entry (FTASKS 7.10)."""
    reg = OperatorRegistry.build(native=FIXTURE_OPERATORS)
    monkeypatch.setattr(operator_port, "_registry", reg)
    monkeypatch.setattr(registry_module, "_process", reg)
    version = reg.current_version("dj_text_length_filter")
    assert version is not None
    bad = await client.post(
        f"{OPS}/dj_text_length_filter/{version}/validate", json={"params": {"min_len": "abc"}}
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["details"]["errors"][0]["pointer"] == "/min_len"
    recipe = {
        "format": "dw.recipe/v1",
        "steps": [
            {"operator": "dj_text_length_filter", "version": version, "params": {"min_len": "abc"}}
        ],
    }
    saved = await client.post("/api/v1/recipes", json={"name": "dj", "body": recipe})
    assert saved.status_code == 422 and "params_invalid" in saved.text
    with get_sync_engine().connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM dw_jobs")).scalar_one() == 0
