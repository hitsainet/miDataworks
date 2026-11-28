"""Wiring: the registry is installed where production starts, and steps dispatch as designed
(FTASKS 4.2, 6.1, 6.4, 12.1; reachability: a test fails when the wiring is removed)."""

from __future__ import annotations

from typing import Any

import pytest
from celery.signals import worker_init, worker_process_init

from src.core.celery_app import route_for
from src.main import fastapi_app
from src.operators import executor
from src.operators import registry as registry_module
from src.operators.datajuicer import runner as dj_runner
from src.operators.errors import OperatorError
from src.operators.manifest import manifest_hash
from src.operators.native.fixtures import DropShort
from src.operators.registry import OperatorRegistry
from src.services import operator_port
from src.services.operator_port import StepSpec
from tests.support import operator_fixtures as fx


@pytest.fixture
def no_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(operator_port, "_registry", operator_port.NoOperatorsInstalled())
    monkeypatch.setattr(registry_module, "_process", None)


async def test_the_app_lifespan_installs_the_registry(no_registry: None) -> None:
    async with fastapi_app.router.lifespan_context(fastapi_app):
        assert isinstance(operator_port.registry(), OperatorRegistry)


@pytest.mark.parametrize("signal", [worker_init, worker_process_init])
def test_worker_start_installs_the_registry(no_registry: None, signal: Any) -> None:
    import src.workers.operator_tasks  # noqa: F401 - the module Celery includes

    signal.send(sender=None)
    assert isinstance(operator_port.registry(), OperatorRegistry)


def routed_by(app: Any, sig: Any) -> str:
    """The queue ``sig`` lands on when ``app`` sends it — a link is sent by the worker that ran the
    parent, through THAT worker's Celery app (2026-10-07: an unpinned finalize reached the runner's
    own queue, where it is unregistered, and every Data-Juicer version build hung)."""
    queue = app.amqp.router.route(dict(sig.get("options") or {}), sig["task"]).get("queue")
    return str(getattr(queue, "name", queue) or app.conf.task_default_queue)


class Recorder:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, name: str, **options: Any) -> None:
        self.sent.append((name, options))


def _spec(name: str = "fx_drop_short", params: dict[str, Any] | None = None) -> StepSpec:
    reg = fx.registry()
    return StepSpec(
        step_execution_id="00000000-0000-0000-0000-0000000000aa",
        operator=name,
        version="1",
        params=params if params is not None else {"min_len": 5},
        input_dir="runs/j/steps/prev",
        output_dir="runs/j/steps/this",
        step_seed=3,
        job_id="j",
        bindings=[],
        column_roles=dict(fx.ROLES),
        rowkey_scheme="dw.rowkey/v1",
        expected_manifest_hash=str(reg.entry(name, "1").manifest_hash),
    )


def test_dispatch_sends_the_payload_once_with_002s_link(monkeypatch: pytest.MonkeyPatch) -> None:
    """6.1: payload and call count, with a recording app."""
    recorder = Recorder()
    monkeypatch.setattr(executor, "send_task", recorder)
    spec = _spec()
    executor.dispatch_step(fx.registry(), spec, "midataworks.versions.advance_build", ["j"])
    assert len(recorder.sent) == 1
    name, options = recorder.sent[0]
    assert name == "midataworks.operators.step.curation"
    assert options["args"] == [spec.as_payload()]
    link = options["link"]
    assert link["task"] == "midataworks.versions.advance_build"
    assert list(link["args"]) == ["j"] and link["immutable"] is True


def test_dispatch_routes_a_labeling_operator_to_the_labeling_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = Recorder()
    monkeypatch.setattr(executor, "send_task", recorder)
    executor.dispatch_step(
        fx.registry(), _spec("fx_endpoint_probe", {}), "midataworks.versions.advance_build", ["j"]
    )
    assert recorder.sent[0][0] == "midataworks.operators.step.labeling"


def test_dispatch_refuses_invalid_params_and_a_stale_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder()
    monkeypatch.setattr(executor, "send_task", recorder)
    with pytest.raises(OperatorError) as exc:
        executor.dispatch_step(fx.registry(), _spec(params={"min_len": "abc"}), "x.y", ["j"])
    assert exc.value.code == "params_invalid"
    assert exc.value.details["errors"][0]["pointer"] == "/min_len"
    stale = _spec()
    stale = StepSpec(**{**stale.as_payload(), "expected_manifest_hash": "0" * 64})
    with pytest.raises(OperatorError) as exc:
        executor.dispatch_step(fx.registry(), stale, "x.y", ["j"])
    assert exc.value.code == "manifest_mismatch"
    assert recorder.sent == []


def test_every_step_task_name_routes_to_its_queue() -> None:
    """Registry-driven routing (6.4): each queue a manifest can name reaches its task's queue."""
    for queue, task in executor.TASK_FOR_QUEUE.items():
        assert route_for(task) == queue
    assert route_for(executor.FINALIZE_DATAJUICER) == "curation"
    assert route_for("midataworks.operators.preview") == "preview"


def test_the_executor_rechecks_params_in_the_worker(data_dir: Any) -> None:
    """FR-003.7: the worker validates again; an engine never sees an unvalidated value."""
    reg = fx.registry()
    spec = fx.stage_spec(reg, "fx_drop_short", {"min_len": "abc"}, data=fx.table())
    with pytest.raises(OperatorError) as exc:
        executor.execute_in_process(spec, registry=reg)
    assert exc.value.code == "params_invalid"


def test_the_executor_refuses_a_mismatched_manifest_hash(data_dir: Any) -> None:
    reg = fx.registry()
    spec = fx.stage_spec(reg, "fx_drop_short", {"min_len": 3}, data=fx.table())
    from dataclasses import replace

    stale = replace(spec, expected_manifest_hash=manifest_hash(DropShort.manifest)[::-1])
    with pytest.raises(OperatorError) as exc:
        executor.execute_in_process(stale, registry=reg)
    assert exc.value.code == "manifest_mismatch"


def test_a_datajuicer_step_goes_to_the_runner_with_the_finaliser_linked_both_ways(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Queue isolation (ADR-010): the step is SENT to the Data-Juicer image by name; the backend's
    finaliser follows on success and on failure, then calls 002's callback."""
    from src.operators.registry import DJ_CATALOGUE

    reg = OperatorRegistry.build(
        native=(), catalogues=((DJ_CATALOGUE, "datajuicer"),), entry_points=()
    )
    version = reg.current_version("dj_text_length_filter")
    assert version is not None
    entry = reg.entry("dj_text_length_filter", version)
    spec = StepSpec(
        **{
            **_spec().as_payload(),
            "operator": "dj_text_length_filter",
            "version": version,
            "params": {"min_len": 5},
            "expected_manifest_hash": str(entry.manifest_hash),
        }
    )
    recorder = Recorder()
    monkeypatch.setattr(executor, "send_task", recorder)
    executor.dispatch_step(reg, spec, "midataworks.versions.advance_build", ["j"])
    assert len(recorder.sent) == 1
    name, options = recorder.sent[0]
    assert name == "midataworks.datajuicer.step" and route_for(name) == "datajuicer"
    payload = options["args"][0]
    assert payload["op_name"] == "text_length_filter" and payload["params"] == {"min_len": 5}
    assert payload["params_schema"]["properties"]["min_len"]["type"] == "integer"
    for key, failed in (("link", False), ("link_error", True)):
        signature = options[key]
        assert signature["task"] == executor.FINALIZE_DATAJUICER
        assert list(signature["args"][1:]) == ["midataworks.versions.advance_build", ["j"]]
        assert signature["kwargs"] == ({"failed": True} if failed else {})
        assert routed_by(dj_runner.app, signature) == "curation"
    with pytest.raises(OperatorError):
        executor.execute_in_process(spec, registry=reg)  # never runs in the backend process


def test_the_finaliser_always_sends_002s_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.operators.datajuicer import finalize
    from src.workers import operator_tasks

    recorder = Recorder()
    monkeypatch.setattr(executor, "send_task", recorder)
    monkeypatch.setattr(
        finalize, "finalize_step", lambda spec, failed=False: {"status": "failed", "failed": failed}
    )
    out = operator_tasks.finalize_datajuicer.run(
        _spec().as_payload(), "midataworks.versions.advance_build", ["j"], failed=True
    )
    assert out == {"status": "failed", "failed": True}
    assert recorder.sent == [("midataworks.versions.advance_build", {"args": ["j"]})]


@pytest.mark.parametrize("runner_app", ["datajuicer", "designer"])
def test_the_preview_callback_reaches_the_preview_queue_from_either_runner(runner_app: str) -> None:
    """The preview route links ``midataworks.operators.preview`` to a runner's preview task; the
    runner's app sends it, so the queue must be pinned on the signature."""
    from src.api.v1.endpoints.operators import PREVIEW_TASK
    from src.core.celery_app import linked_signature
    from src.operators.data_designer import runner as dd_runner

    app = dj_runner.app if runner_app == "datajuicer" else dd_runner.app
    sig = linked_signature(PREVIEW_TASK, args=[{}])
    assert routed_by(app, sig) == route_for(PREVIEW_TASK) == "preview"
