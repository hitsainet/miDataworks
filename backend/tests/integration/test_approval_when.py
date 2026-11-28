"""Every gate states its condition: ``x-approval-when`` (022 FR-022.30; suite A19, A33; 022-D26, D27).

1. **Registry pin.** Derived from the LIVE app (no hand list of routes): every route whose endpoint
   carries ``__dw_approval_action__`` publishes ``x-approval-when``, its value is in
   ``WHEN_MARKERS`` and equals the endpoint's recorded marker. On 2026-11-16 there are 21 gated
   operations (asserted ``>= 20``), exactly seven of them conditional, named below.
2. **The marker means what the predicate does.** The field marker's cases are driven by the LIVE
   marker: each case says whether its condition holds (the field is sent), and the expected answer
   is "waits" when the marker is ``always`` or the condition holds, else "runs" with no approval.
   So marking ``POST /api/v1/sources/hf/preview`` ``always`` turns its no-token case red. An
   ``always`` route's valid agent call waits. Each state marker's pair needs its feature's
   fixtures, so it lives beside them; ``STATE_PAIRS`` names both halves of every pair, the AST
   check fails if one is deleted, and the live markers of those routes are pinned here.
3. **Import-time refusal.** A ``when=`` without a marker, a conditional marker without a ``when=``,
   and a marker outside the vocabulary are ``ValueError`` at decoration.
"""

from __future__ import annotations

import ast
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.routing import APIRoute
from sqlalchemy import func, select

from src.core.agent_origin import WHEN_MARKERS, requires_approval_when_agent
from src.core.database import sync_session_factory
from src.models.approval import Approval
from tests.support.hf_mock import COLBERT
from tests.support.source_fixtures import HfEnv, hf_env

__all__ = ["hf_env"]

TESTS = Path(__file__).resolve().parents[1]
AGENT = {"X-Dataworks-Agent": "agent:dataworks-mcp"}

#: The seven conditional gates on 2026-11-16 (FPRD 022 section I), with their markers.
CONDITIONAL = {
    ("POST", "/api/v1/sources/hf/preview"): "field:body.access_token",
    ("POST", "/api/v1/sources/hf"): "field:body.access_token",
    ("POST", "/api/v1/versions"): "state:agent_label_plan_or_sensitivity_lowering",
    ("POST", "/api/v1/recipes/{recipe_id}/build"): (
        "state:agent_label_plan_or_sensitivity_lowering"
    ),
    ("POST", "/api/v1/label-runs"): "state:agent_label_plan_or_paid_endpoint",
    ("POST", "/api/v1/label-runs/{run_id}/resume"): "state:paid_endpoint",
    ("POST", "/api/v1/code-rules"): "state:code_hash_unapproved",
}


def live_gated() -> dict[tuple[str, str], tuple[str, str | None]]:
    """``{(METHOD, template): (endpoint's recorded marker, published marker)}`` from the live app.

    The endpoints come from the routers ``create_app`` includes (this FastAPI wraps an included
    router, so ``app.routes`` holds no ``APIRoute``); the published marker from the app's OpenAPI.
    The two sets are compared, so a gated operation neither side sees cannot hide.
    """
    from src.main import ROUTERS, create_app

    operations = create_app().openapi()["paths"]
    out: dict[tuple[str, str], tuple[str, str | None]] = {}
    for router in ROUTERS:
        for route in router.routes:
            if not isinstance(route, APIRoute):
                continue
            if not getattr(route.endpoint, "__dw_approval_action__", None):
                continue
            for method in route.methods:
                operation = operations[route.path][method.lower()]
                out[(method, route.path)] = (
                    route.endpoint.__dw_approval_when__,
                    operation.get("x-approval-when"),
                )
    published = {
        (method.upper(), path)
        for path, ops in operations.items()
        for method, op in ops.items()
        if isinstance(op, dict) and op.get("x-approval-action")
    }
    assert published == set(out), sorted(published ^ set(out))
    return out


def live_marker(method: str, template: str) -> str:
    from src.main import fastapi_app

    marker: str = fastapi_app.openapi()["paths"][template][method.lower()]["x-approval-when"]
    return marker


# -- 1. the registry pin -------------------------------------------------------------------------


def test_every_gated_operation_publishes_its_recorded_marker() -> None:
    gated = live_gated()
    assert len(gated) >= 20, f"only {len(gated)} gated operations found: the scan is wrong"
    for route, (recorded, published) in sorted(gated.items()):
        assert published is not None, f"{route} carries x-approval-action and no x-approval-when"
        assert published in WHEN_MARKERS, (route, published)
        assert published == recorded, (route, recorded, published)


def test_exactly_the_seven_conditional_gates_are_not_always() -> None:
    gated = live_gated()
    conditional = {
        route: published for route, (_, published) in gated.items() if published != "always"
    }
    assert conditional == CONDITIONAL
    assert len(gated) - len(conditional) >= 13, "the unconditional gates went missing"


def test_every_marker_of_the_vocabulary_is_in_use() -> None:
    used = {published for _, published in live_gated().values()}
    assert used == set(WHEN_MARKERS), sorted(set(WHEN_MARKERS) - used)


def test_the_marker_endpoints_record_is_what_the_decorator_was_given() -> None:
    """Every decorated endpoint built without ``when`` records ``always``."""
    from src.core.agent_origin import EXECUTORS

    assert EXECUTORS, "no gated endpoint registered"
    for route, (recorded, _) in live_gated().items():
        assert recorded in WHEN_MARKERS, route


# -- 2. the marker means what the predicate does --------------------------------------------------


def approvals() -> int:
    with sync_session_factory()() as db:
        return int(db.execute(select(func.count()).select_from(Approval)).scalar_one())


Call = Callable[[httpx.AsyncClient], Awaitable[httpx.Response]]


async def _preview(client: httpx.AsyncClient, token: str | None) -> httpx.Response:
    body: dict[str, Any] = {"repo_id": COLBERT}
    if token:
        body["access_token"] = token
    return await client.post("/api/v1/sources/hf/preview", json=body, headers=AGENT)


async def _import(client: httpx.AsyncClient, token: str | None) -> httpx.Response:
    body: dict[str, Any] = {"repo_id": COLBERT}
    if token:
        body["access_token"] = token
    return await client.post("/api/v1/sources/hf", json=body, headers=AGENT)


#: (method, template, the condition holds, call). A field marker's pair runs here.
FIELD_CASES: list[
    tuple[str, str, bool, Callable[[httpx.AsyncClient], Awaitable[httpx.Response]]]
] = [
    ("POST", "/api/v1/sources/hf/preview", False, lambda c: _preview(c, None)),
    ("POST", "/api/v1/sources/hf/preview", True, lambda c: _preview(c, "hf_agent_token_022")),
    ("POST", "/api/v1/sources/hf", False, lambda c: _import(c, None)),
    ("POST", "/api/v1/sources/hf", True, lambda c: _import(c, "hf_agent_token_022")),
]


@pytest.mark.parametrize(
    "case", FIELD_CASES, ids=[f"{m} {t} condition={c}" for m, t, c, _ in FIELD_CASES]
)
async def test_a_field_marker_waits_exactly_when_the_field_is_sent(
    case: tuple[str, str, bool, Call],
    client: httpx.AsyncClient,
    hf_env: HfEnv,
    operator_name: str,
) -> None:
    method, template, condition, call = case
    marker = live_marker(method, template)
    expect_wait = marker == "always" or condition
    before = approvals()
    response = await call(client)
    if expect_wait:
        assert response.status_code == 202, (marker, response.status_code, response.text)
        assert response.json().get("status") == "pending", response.json()
        assert approvals() == before + 1
    else:
        assert response.status_code in (200, 202), response.text
        assert "approval_id" not in response.json(), (marker, response.json())
        assert approvals() == before, f"{template} filed an approval with marker {marker}"


async def test_an_always_gate_waits_on_a_valid_agent_call(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    """``always`` (``PUT /api/v1/settings/{key}``, no ``when=``): a valid agent call waits."""
    assert live_marker("PUT", "/api/v1/settings/{key}") == "always"
    before = approvals()
    response = await client.put(
        "/api/v1/settings/hf_token", json={"value": "hf_agent_token_022"}, headers=AGENT
    )
    assert response.status_code == 202, response.text
    assert response.json()["action"] == "secret_write"
    assert approvals() == before + 1


#: The state markers' pairs, each beside its feature's fixtures: (file under tests/, test name).
#: ``waits`` has the condition true and asserts 202 with an approval; ``runs`` has it false and
#: asserts the call ran with no approval.
STATE_PAIRS: dict[str, dict[str, list[tuple[str, str]]]] = {
    # 021 23.2: the builds also wait when an agent declares data not sensitive over inputs not
    # already so (``build_gate_when``); each half of the "or" has its own waiting test.
    "state:agent_label_plan_or_sensitivity_lowering": {
        "waits": [
            (
                "integration/test_build_pipeline.py",
                "test_an_agent_build_over_the_threshold_waits_for_approval",
            ),
            (
                "integration/test_build_pipeline.py",
                "test_an_agent_recipe_build_over_the_threshold_waits_for_approval",
            ),
            ("integration/test_sensitivity_gate.py", "test_agent_lowering_waits"),
        ],
        "runs": [
            (
                "integration/test_build_pipeline.py",
                "test_an_agent_build_under_the_threshold_runs_and_is_recorded",
            ),
        ],
    },
    "state:agent_label_plan_or_paid_endpoint": {
        "waits": [
            (
                "integration/labeling/test_approval_gate.py",
                "test_agent_5001_rows_waits_and_5000_starts",
            ),
            (
                "integration/labeling/test_paid_classifier_gate.py",
                "test_an_agent_ten_row_run_on_a_paid_classifier_waits",
            ),
        ],
        "runs": [
            (
                "integration/labeling/test_approval_gate.py",
                "test_agent_5001_rows_waits_and_5000_starts",
            ),
            (
                "integration/labeling/test_paid_classifier_gate.py",
                "test_the_same_run_on_a_local_classifier_starts_and_an_operator_is_never_gated",
            ),
        ],
    },
    "state:paid_endpoint": {
        "waits": [
            (
                "integration/labeling/test_partial_second_opinion.py",
                "test_a_run_failing_part_way_keeps_its_scores_and_a_resume_asks_for_the_rest",
            ),
        ],
        "runs": [
            ("integration/labeling/test_approval_gate.py", "test_a_resume_needs_no_approval"),
        ],
    },
    "state:code_hash_unapproved": {
        "waits": [
            (
                "integration/code_rules/test_save_gate.py",
                "test_an_agent_save_of_a_new_hash_waits_and_writes_nothing",
            ),
        ],
        "runs": [
            (
                "integration/code_rules/test_save_gate.py",
                "test_an_approved_hash_saves_without_a_new_approval_and_a_case_edit_too",
            ),
        ],
    },
}


def _test_names(rel: str) -> set[str]:
    tree = ast.parse((TESTS / rel).read_text())
    return {n.name for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)}


def test_every_state_marker_has_its_pair_beside_its_feature() -> None:
    state_markers = {m for m in WHEN_MARKERS if m.startswith("state:")}
    assert set(STATE_PAIRS) == state_markers
    for marker, halves in STATE_PAIRS.items():
        assert halves["waits"] and halves["runs"], marker
        for rel, name in halves["waits"] + halves["runs"]:
            assert name in _test_names(rel), f"{rel} has no {name}: {marker}'s pair is gone"


def test_the_state_routes_publish_the_marker_their_pair_proves() -> None:
    for route, marker in CONDITIONAL.items():
        assert live_marker(*route) == marker, route


# -- 3. import-time refusal -----------------------------------------------------------------------


async def _stub(**_: Any) -> None:
    return None


def test_a_when_without_a_marker_is_refused_at_import() -> None:
    with pytest.raises(ValueError, match="when_marker"):
        requires_approval_when_agent("hub_push", when=lambda values, db: True)


def test_a_conditional_marker_without_a_when_is_refused() -> None:
    with pytest.raises(ValueError, match="needs its when"):
        requires_approval_when_agent("hub_push", when_marker="state:paid_endpoint")


def test_a_marker_outside_the_vocabulary_is_refused() -> None:
    with pytest.raises(ValueError, match="not a registered"):
        requires_approval_when_agent(
            "hub_push", when=lambda values, db: True, when_marker="state:build_needs_approval"
        )
