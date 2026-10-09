"""Feature 007 is reachable through every live registry it claims (007 FTASKS 15.1; FTDD 10.2).

Each assertion reads the LIVE registry the production path reads — never that a module imports —
so deleting a registration line turns this file red (the controls are in the review record).
"""

from __future__ import annotations

from datetime import timedelta

from src.core.celery_app import BEAT_SCHEDULE, celery_app, create_celery_app, route_for
from src.core.job_kinds import JOB_KINDS


def test_the_two_job_kinds_are_registered_with_task_room_and_limit() -> None:
    run = JOB_KINDS["generation_run"]
    assert run.task_name == "midataworks.generation.run"
    assert run.room("gr_1") == "dataworks/generation-runs/gr_1"
    assert run.janitor_heartbeat_limit == timedelta(minutes=15)
    report = JOB_KINDS["diversity_report"]
    assert report.task_name == "midataworks.generation.diversity_report"
    assert report.room("job_1") == "dataworks/diversity-reports/job_1"


def test_the_tasks_are_registered_and_routed() -> None:
    celery_app.loader.import_default_modules()
    names = set(celery_app.tasks)
    for name, queue in (
        ("midataworks.generation.run", "labeling"),
        ("midataworks.generation.diversity_report", "labeling"),
        ("midataworks.generation.diversity_sweep", "default"),
    ):
        assert name in names, name
        assert route_for(name) == queue, name


def test_the_sweep_is_on_beat() -> None:
    entry = BEAT_SCHEDULE["generation-diversity-sweep"]
    assert entry["task"] == "midataworks.generation.diversity_sweep"
    assert "generation-diversity-sweep" in create_celery_app().conf.beat_schedule


def test_the_operators_are_in_the_production_registry() -> None:
    from src.operators.registry import OperatorRegistry

    registry = OperatorRegistry.build(catalogues=(), entry_points=())
    for name in (
        "native_chat_generate",
        "dw_generated_rows",
        "dw_judge_filter",
        "dw_pair_filter",
        "dw_pair_from_scores",
    ):
        assert registry.require_allowed(name, "1").manifest is not None, name
    assert (
        "applies_to"
        in registry.entry("cluster_balancer", "1.1.0").manifest.params_schema["properties"]
    )


def test_the_routes_are_in_the_live_app() -> None:
    from src.main import fastapi_app

    paths = fastapi_app.openapi()["paths"]
    for path, method in (
        ("/api/v1/generation-templates", "post"),
        ("/api/v1/generation-runs", "post"),
        ("/api/v1/generation-runs/plan", "post"),
        ("/api/v1/generation-runs/preview", "post"),
        ("/api/v1/steering-settings/compare", "post"),
        ("/api/v1/generation-runs/independence-check", "post"),
        ("/api/v1/generation-runs/{run_id}/candidate-build", "post"),
        ("/api/v1/versions/{version_id}/diversity", "get"),
    ):
        assert method in paths[path], path


def test_install_wires_bindings_metrics_and_the_delete_guard() -> None:
    from src.services import bindings, version_delete_service
    from src.services.calibration import registry
    from src.services.generation import install, run_service

    install.install()
    assert bindings.BINDING_RESOLVERS["generation_run"] is run_service.binding_record
    assert "dw_generation_runs" in version_delete_service.REFERENCE_CHECKERS
    assert {"distinct_1", "distinct_2", "embedding_spread", "cluster_coverage"} <= {
        s.metric_id for s in registry.gate_metrics("gate3")
    }


def test_the_lifespan_and_the_worker_install_feature_007() -> None:
    """The two production entry points call ``install`` (asserted by the call, not the text)."""
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[3] / "src"
    for path, function in (
        (src / "main.py", "lifespan"),
        (src / "workers" / "generation_tasks.py", "_install_generation"),
    ):
        tree = ast.parse(path.read_text())
        fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == function
        )
        calls = [
            n
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "install"
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id in ("generation_install", "install")
        ]
        assert calls, f"{path.name}:{function} must call feature 007's install()"
