"""The review wiring that keeps P-10 true, asserted as CALLS (006 FTASKS 15.4, 15.6)."""

from __future__ import annotations

import ast
import inspect
from typing import Any

from src.api.v1.endpoints import review as review_routes
from src.services.calibration import set_service
from src.services.review import audit_service, decision_service


def call_names(fn: Any) -> list[str]:
    """Call names in SOURCE order (``ast.walk`` is breadth-first, so sort by position)."""
    found: list[tuple[int, int, str]] = []
    for node in ast.walk(ast.parse(inspect.getsource(fn).lstrip())):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            found.append((node.lineno, node.col_offset, name))
    return [name for _, _, name in sorted(found)]


def test_the_decision_route_records_through_the_service() -> None:
    assert call_names(review_routes.record_review_decision).count("record") == 1
    assert "resolve_who" in call_names(review_routes.record_review_decision)


def test_the_service_calls_allowed_then_validate_before_inserting() -> None:
    names = call_names(decision_service.record)
    assert "allowed" in names and "validate" in names
    assert names.index("allowed") < names.index("validate") < names.index("ReviewDecision")


def test_from_review_and_audit_filter_on_operator_origin() -> None:
    """The WHERE clauses that keep agent decisions out of human labels (FR-006.24)."""
    for fn in (set_service.from_review, audit_service._decisions_query):
        src = inspect.getsource(fn)
        assert 'decided_by_origin == "operator"' in src, fn.__name__
