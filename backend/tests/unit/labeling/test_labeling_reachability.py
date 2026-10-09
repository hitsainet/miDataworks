"""Feature 005 reachability from LIVE registries (005 FTASKS 16.1, 16.2; ADR-021).

Each test reads the registry production reads — the app's OpenAPI paths, Celery's task registry,
the job-kind registry, 003's operator registry, 002's binding resolvers, 003's endpoint port after
the app's lifespan, the preflight list — never a hand-kept list of what it hopes is there. The
negative controls (delete each registration, require a red) are recorded in
0xcc/reviews/005_implementation_controls_2026-10-07.md.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.core.celery_app import BEAT_SCHEDULE, celery_app, route_for
from src.core.job_kinds import JOB_KINDS

SRC = Path(__file__).resolve().parents[3] / "src"

KINDS = {
    "label_run": "midataworks.labeling.run_label_run",
    "label_preview": "midataworks.labeling.run_label_preview",
    "label_rederive": "midataworks.labeling.run_rederive",
    "label_aggregate": "midataworks.labeling.run_aggregate",
}


def test_each_job_kind_names_a_registered_labeling_task() -> None:
    celery_app.loader.import_default_modules()
    for kind, task in KINDS.items():
        assert JOB_KINDS[kind].task_name == task
        assert task in celery_app.tasks
        assert route_for(task) == "labeling"
    assert JOB_KINDS["label_run"].room("lr_1") == "dataworks/label-runs/lr_1"


def test_the_lease_beat_entry_is_scheduled_and_routed_to_default() -> None:
    celery_app.loader.import_default_modules()
    entry = BEAT_SCHEDULE["renew-model-leases"]
    assert entry["task"] == "midataworks.labeling.renew_model_leases"
    assert entry["task"] in celery_app.tasks
    assert entry["schedule"].total_seconds() == 300
    assert route_for(entry["task"]) == "default"
    assert "renew-model-leases" in celery_app.conf.beat_schedule


def test_the_threshold_labeler_is_in_the_operator_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core.config import get_settings
    from src.operators.registry import OperatorRegistry

    monkeypatch.setattr(get_settings(), "operator_test_fixtures", False)
    registry = OperatorRegistry.build()
    entry = registry.require_allowed("threshold_labeler", "1")
    assert entry.manifest is not None
    assert entry.manifest.binding_kinds == ("label_run",)


def test_the_label_run_binding_resolver_is_registered_by_the_app() -> None:
    import src.main  # noqa: F401 - the app's import registers it
    from src.services.bindings import BINDING_RESOLVERS
    from src.services.label_run_service import binding_record

    assert BINDING_RESOLVERS["label_run"] is binding_record


async def test_the_lifespan_installs_feature_005_into_the_endpoint_port() -> None:
    from src.main import fastapi_app
    from src.operators import endpoint_port
    from src.services.labeling_ports import PortLeaseManager, PortResolver

    previous_r = endpoint_port.install_resolver(endpoint_port.UnconfiguredResolver())
    previous_l = endpoint_port.install_lease_manager(endpoint_port.NoLeaseManager())
    try:
        async with fastapi_app.router.lifespan_context(fastapi_app):
            assert isinstance(endpoint_port.resolver(), PortResolver)
            assert isinstance(endpoint_port.lease_manager(), PortLeaseManager)
    finally:
        endpoint_port.install_resolver(previous_r)
        endpoint_port.install_lease_manager(previous_l)


def test_the_worker_start_hook_installs_feature_005(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.services import labeling_ports
    from src.workers import label_run_tasks

    calls: list[int] = []
    monkeypatch.setattr(labeling_ports, "install", lambda: calls.append(1))
    label_run_tasks._install_labeling()
    assert calls == [1]
    receivers = [r[1]() for r in __import__("celery.signals").signals.worker_process_init.receivers]
    assert label_run_tasks._install_labeling in receivers


def test_the_preflight_registry_imports_its_registrations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services import label_run_preflight, label_run_preflight_registrations

    seen: list[object] = []
    # Only the probe runs: the registered feature checks need a real plan context.
    monkeypatch.setattr(label_run_preflight, "PREFLIGHT_CHECKS", [lambda ctx: seen.append(ctx)])
    label_run_preflight.run_checks(None)  # type: ignore[arg-type]
    assert len(seen) == 1
    # Feature 007 registered JUDGE_IS_GENERATOR (007 FTASKS 8.3, 13.5).
    assert label_run_preflight_registrations.REGISTRATION_MODULES == (
        "src.services.generation.independence",
    )


# --- call sites (FTASKS 16.2): each pure rule is CALLED at its single site ---------------------


def _calls(path: Path, name: str) -> int:
    tree = ast.parse(path.read_text())
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Attribute) and func.attr == name) or (
                isinstance(func, ast.Name) and func.id == name
            ):
                count += 1
    return count


SITES = {
    "decide_binary": ["services/label_run_engine.py", "operators/native/threshold_labeler.py"],
    "decide_top_label": ["services/label_run_engine.py"],
    "approval_needed": ["services/label_run_service.py"],
    "swap_and_agree": ["services/label_run_engine.py"],
    "parse_failure_exceeded": ["services/label_run_engine.py"],
    "aggregate_verdict": ["services/label_run_engine.py"],
    "wilson_interval": ["services/label_run_engine.py"],
    "fingerprint": ["services/label_run_service.py", "services/label_run_engine.py"],
    "reuse_allowed": ["services/label_run_service.py", "services/label_run_engine.py"],
    "check_binding": [
        "clients/labelers/factory.py",
        "services/label_run_service.py",
        "api/v1/endpoints/labeling.py",
    ],
}


@pytest.mark.parametrize("rule", sorted(SITES))
def test_each_rule_is_called_only_at_its_sites(rule: str) -> None:
    found = sorted(
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if p.name != "labeling_rules.py" and _calls(p, rule) > 0
    )
    assert found == sorted(SITES[rule])


def test_decide_binary_is_called_once_in_the_engine() -> None:
    """Every engine outcome (scoring, reuse, re-derive, sample, keep share) goes through ONE
    call — ``decide_outcome``."""
    assert _calls(SRC / "services/label_run_engine.py", "decide_binary") == 1


def test_only_the_holder_calls_the_lease_client() -> None:
    """FTASKS 10.8: two features building their own lease logic is the risk the holder removes."""
    users = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and "millm_lease_client" in node.module
            ):
                users.append(str(path.relative_to(SRC)))
            if isinstance(node, ast.ImportFrom) and any(
                a.name == "millm_lease_client" for a in node.names
            ):
                users.append(str(path.relative_to(SRC)))
    assert sorted(set(users)) == ["services/model_lease_holder.py"]
