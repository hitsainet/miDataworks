"""Feature 004 is reachable in the LIVE registries, not only importable (FR-004.46; FTASKS 14.6).

Each test reads the object production reads: the operator registry built the production way, the
FastAPI app's OpenAPI paths, Celery's task registry and router, the Beat schedule, the job-kind
registry. Deleting a registration line turns one of these red (controls C32–C36).
"""

from __future__ import annotations

from datetime import timedelta

from src.core.celery_app import celery_app, create_celery_app, route_for
from src.core.config import get_settings
from src.operators.native.curation import CURATION_OPERATORS, REPORT_OPERATORS
from src.operators.registry import OperatorRegistry

TASKS = (
    "midataworks.curation.run_report",
    "midataworks.curation.post_version",
    "midataworks.curation.sweep_unaudited",
)
ROUTES = {
    ("POST", "/api/v1/versions/{version_id}/shortcut-audit"),
    ("GET", "/api/v1/versions/{version_id}/shortcut-audit"),
    ("GET", "/api/v1/versions/{version_id}/shortcut-audit/cells"),
    ("POST", "/api/v1/versions/{version_id}/profile"),
    ("GET", "/api/v1/versions/{version_id}/profile"),
    ("POST", "/api/v1/versions/{version_id}/leakage"),
    ("GET", "/api/v1/versions/{version_id}/leakage"),
    ("GET", "/api/v1/versions/{version_id}/leakage/pairs"),
    ("POST", "/api/v1/versions/{version_id}/contamination"),
    ("GET", "/api/v1/versions/{version_id}/contamination"),
    ("POST", "/api/v1/versions/{version_id}/trl-validation"),
    ("GET", "/api/v1/datasets/{dataset_id}/shortcut-level"),
    ("PUT", "/api/v1/datasets/{dataset_id}/shortcut-level"),
    ("DELETE", "/api/v1/datasets/{dataset_id}/shortcut-level"),
    ("GET", "/api/v1/settings/shortcut-level"),
    ("PUT", "/api/v1/settings/shortcut-level"),
    ("GET", "/api/v1/curation/benchmarks"),
}


def test_curation_operators_in_live_registry() -> None:
    production = OperatorRegistry.build(catalogues=(), entry_points=())
    states = {e.name: s for e, s in production.entries()}
    for cls in CURATION_OPERATORS:
        assert states.get(cls.manifest.name) == "allowed", cls.manifest.name
    # FTDD §6.2's catalogue minus dedup_embedding (phase 8, P-18), plus the native ngram_repetition
    # and chat_json_parser (2026-10-08 live finding: a chat stored as JSON text).
    assert {c.manifest.name for c in CURATION_OPERATORS} == {
        "profile",
        "shortcut_audit",
        "leakage_check",
        "contamination_check",
        "cluster",
        "normaliser",
        "chat_json_parser",
        "dedup_exact",
        "dedup_minhash",
        "length_band",
        "turn_count_band",
        "empty_content",
        "metadata_value_filter",
        "decontaminate",
        "trl_validate",
        "cell_balancer",
        "cluster_balancer",
        "split",
        "ngram_repetition",
    }
    assert set(REPORT_OPERATORS) == {
        "shortcut_audit",
        "leakage",
        "profile",
        "contamination",
        "clusters",
    }


def test_every_route_is_in_the_live_app() -> None:
    from src.main import fastapi_app

    paths = fastapi_app.openapi()["paths"]
    live = {(m.upper(), p) for p, ops in paths.items() for m in ops}
    assert ROUTES <= live, sorted(ROUTES - live)


def test_settings_level_route_is_not_swallowed_by_settings_key() -> None:
    """/settings/shortcut-level must reach feature 004's handler, not /settings/{key}."""
    from src.main import fastapi_app

    put = fastapi_app.openapi()["paths"]["/api/v1/settings/shortcut-level"]["put"]
    assert put["operationId"].startswith("set_global_level")


def test_tasks_are_registered_and_routed_to_curation() -> None:
    celery_app.loader.import_default_modules()
    for name in TASKS:
        assert name in celery_app.tasks, name
        assert route_for(name) == "curation", name


def test_beat_runs_the_sweeper_on_the_configured_interval(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(get_settings(), "curation_sweep_interval_s", 17.0)
    app = create_celery_app()
    entry = app.conf.beat_schedule["curation-sweep-unaudited"]
    assert entry["task"] == "midataworks.curation.sweep_unaudited"
    assert entry["schedule"] == timedelta(seconds=17)


def test_job_kind_is_registered_with_its_task() -> None:
    from src.core.job_kinds import get_job_kind

    kind = get_job_kind("curation_report")
    assert kind.task_name == "midataworks.curation.run_report"
    assert kind.room("j1") == "dataworks/curation-reports/j1"


def test_008_seam_reaches_the_api_functions() -> None:
    from src.services.publishing import feature_seams

    module = feature_seams.load_owner(feature_seams.CURATION_API)
    assert module is not None
    for name in ("evaluate_warnings", "check_leakage", "validate_trl"):
        assert callable(getattr(module, name)), name
