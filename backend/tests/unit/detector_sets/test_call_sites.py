"""Each pure decision is CALLED by the code that ships (FTASKS 4.7, 6.3, 7.6, 13.3; FTID 009
section 1). Asserted by walking the AST for a call, never by a substring of the source: a comment
naming a function is not a call (the 033 survivor pattern)."""

from __future__ import annotations

import ast
import inspect
from collections.abc import Iterable
from types import ModuleType

from src.services.detector_sets import (
    agreement,
    results_service,
    send_service,
    set_service,
)
from src.workers import detector_send_tasks


def calls(module: ModuleType) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute):
                base = f.value
                prefix = (
                    base.id
                    if isinstance(base, ast.Name)
                    else (base.attr if isinstance(base, ast.Attribute) else "")
                )
                out.add(f"{prefix}.{f.attr}")
            elif isinstance(f, ast.Name):
                out.add(f.id)
    return out


def imports(module: ModuleType) -> Iterable[str]:
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            yield (node.module or "") + ":" + ",".join(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            yield ",".join(a.name for a in node.names)


def test_set_service_calls_the_rules_and_004() -> None:
    found = calls(set_service)
    for name in (
        "checks.evaluate",
        "length.overlap",
        "label_rules.check_mapping",
        "curation_seam.check_leakage",
        "curation_seam.evaluate_warnings",
        "feature_seams.calibration_status",
    ):
        assert name in found, name


def test_the_curation_seam_calls_004_itself() -> None:
    from src.services.detector_sets import curation_seam

    found = calls(curation_seam)
    assert {"curation.evaluate_warnings", "curation.check_leakage"} <= found


def test_send_service_gates_on_send_allowed_and_builds_the_digest() -> None:
    found = calls(send_service)
    for name in (
        "checks.send_allowed",
        "plan.build_plan",
        "plan.approval_digest",
        "publish_service.request_digest",
    ):
        assert name in found, name


def test_the_worker_publishes_through_008_and_compares_counts() -> None:
    found = calls(detector_send_tasks)
    for name in (
        "publish_service.request_publish",
        "publish_tasks.run_publish_job",
        "client.download",
        "client.register_view",
        "plan.registration_body",
        "capabilities.read",
        "record_progress",
    ):
        assert name in found, name


def test_results_service_maps_accepts_and_filters_marks() -> None:
    found = calls(results_service)
    for name in (
        "results_mapping.figures_from_report",
        "paired.accept_source",
        "reward_marks.marked_probe_ids",
    ):
        assert name in found, name


def test_agreement_calls_no_mistudio() -> None:
    """T-48: no miStudio judge run is started; the module cannot reach miStudio at all."""
    assert not any("mistudio" in i for i in imports(agreement))
