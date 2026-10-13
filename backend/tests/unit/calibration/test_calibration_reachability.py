"""006 wiring, asserted in the LIVE registries and by the call shape (006 FTASKS 15.1 – 15.4).

Route presence is in ``tests/unit/test_reachability.py`` (EXPECTED_ROUTES_006); this module adds
the caller shapes: each route calls its service with the payload, exactly once, and the in-process
callers 008 depends on are asserted by walking the AST for a CALL (not a name in a comment).
"""

from __future__ import annotations

import ast
import inspect
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from src.core.celery_app import celery_app, route_for
from src.core.job_kinds import JOB_KINDS
from src.main import fastapi_app


def calls_in(fn: Any) -> list[str]:
    tree = ast.parse(inspect.getsource(fn).lstrip())
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            out.append(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))
    return out


def test_the_task_is_registered_routed_and_named_by_its_job_kind() -> None:
    celery_app.loader.import_default_modules()
    assert "midataworks.calibration.compute_record" in celery_app.tasks
    assert route_for("midataworks.calibration.compute_record") == "default"
    kind = JOB_KINDS["calibration_compute"]
    assert kind.task_name == "midataworks.calibration.compute_record"
    assert kind.room("j1") == "dataworks/calibration/j1"


def test_the_target_route_is_gated_in_the_live_openapi() -> None:
    fastapi_app.openapi_schema = None
    op = fastapi_app.openapi()["paths"]["/api/v1/calibration-targets"]["put"]
    assert op.get("x-approval-action") == "gate_target_write"


def test_no_other_006_route_is_gated() -> None:
    fastapi_app.openapi_schema = None
    for path, ops in fastapi_app.openapi()["paths"].items():
        if path.startswith(("/api/v1/calibration-", "/api/v1/review-")) or path.endswith("/audit"):
            for method, op in ops.items():
                if (method, path) != ("put", "/api/v1/calibration-targets"):
                    assert "x-approval-action" not in op, (method, path)


async def test_compute_route_starts_one_job_with_the_payload(
    client: httpx.AsyncClient, operator_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.api.v1.endpoints import calibration as module
    from src.schemas.calibration import JobStarted

    spy = AsyncMock(return_value=JobStarted(job_id="job_x", room="dataworks/calibration/job_x"))
    monkeypatch.setattr(module.record_service, "start", spy)
    r = await client.post(
        "/api/v1/calibration-records", json={"label_run_id": "lr_1", "calibration_set_id": "cs_1"}
    )
    assert r.status_code == 202 and r.json()["job_id"] == "job_x"
    assert spy.await_count == 1
    body, who = spy.await_args.args[1], spy.await_args.args[2]
    assert (body.label_run_id, body.calibration_set_id) == ("lr_1", "cs_1")
    assert (who.who, who.origin) == (operator_name, "operator")


async def test_status_route_calls_latest_once_with_the_key(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.api.v1.endpoints import calibration as module

    seen: list[dict[str, Any]] = []
    real = module.status_service.latest

    def spy(**kwargs: Any) -> Any:
        seen.append({k: v for k, v in kwargs.items() if k != "session"})
        return real(**kwargs)

    monkeypatch.setattr(module.status_service, "latest", spy)
    r = await client.get("/api/v1/calibration-status?labeler=" + "a" * 64)
    assert r.status_code == 200
    assert seen == [{"identity_hash": "a" * 64, "fingerprint": None}]


def test_008s_seams_call_006s_functions() -> None:
    """C-5, C-6/M-8 and the projection call 006 — asserted as CALLS in 008's own code."""
    from src.services.publishing import build_service, check_inputs, feature_seams

    assert "status" in calls_in(feature_seams.audit_status)
    assert "latest" in calls_in(feature_seams.calibration_status)
    assert "resolve" in calls_in(feature_seams.resolve_effective_labels)
    assert "calibration_status" in calls_in(check_inputs.labeler_checks)
    assert "audit_status" in calls_in(check_inputs.assemble)
    assert "resolve_effective_labels" in calls_in(build_service)
    assert feature_seams.EFFECTIVE_LABEL == "review.effective_label"
    assert feature_seams.CALIBRATION_STATUS == "calibration.status_service"
    assert feature_seams.AUDIT_SERVICE == "review.audit_service"


def test_the_seam_loads_the_real_modules() -> None:
    from src.services.calibration import status_service
    from src.services.publishing import feature_seams
    from src.services.review import audit_service, effective_label

    assert feature_seams.load_owner(feature_seams.CALIBRATION_STATUS) is status_service
    assert feature_seams.load_owner(feature_seams.AUDIT_SERVICE) is audit_service
    assert feature_seams.load_owner(feature_seams.EFFECTIVE_LABEL) is effective_label
