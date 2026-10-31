"""The validators ask the designer worker for Data Designer's own template rules (PII run G4).

2026-10-20: ``dataworks_validate_operator_params`` and ``dataworks_validate_recipe`` called a
``dd_expression`` template valid that Data Designer then refused. Both routes now send
``midataworks.designer.validate`` (the worker's answer is ``runner.validate_templates``, exercised
with the real library in ``designer/tests/test_template_rules.py``) and refuse with the rule.
Here the worker's answer is stood in at ``send_validate``; the payload and the call count are
asserted, and a silent worker is a 503, never "valid".
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from celery.exceptions import TimeoutError as CeleryTimeout

from src.operators.data_designer import validation
from src.operators.registry import DD_CATALOGUE, OperatorRegistry

FORBIDDEN = {
    "valid": False,
    "errors": [
        {
            "field": "expr",
            "rule": "forbidden_tag",
            "message": "Data Designer forbids {% set %} in a template.",
        }
    ],
    "references": {"expr": ["x"]},
    "limits": {"max_nodes": 600, "max_depth": 10, "filters": ["upper"]},
}
VALID = {"valid": True, "errors": [], "references": {"expr": ["text"]}, "limits": {"max_depth": 10}}


class Worker:
    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.payloads: list[dict[str, Any]] = []

    def __call__(self, payload: dict[str, Any]) -> Any:
        self.payloads.append(payload)
        worker = self

        class Result:
            def get(self, timeout: float | None = None) -> Any:
                if isinstance(worker.answer, Exception):
                    raise worker.answer
                return worker.answer

        return Result()


@pytest.fixture
def dd_registry(monkeypatch: pytest.MonkeyPatch) -> OperatorRegistry:
    from src.operators import registry as registry_module
    from src.services import operator_port

    reg = OperatorRegistry.build(
        native=None, catalogues=((DD_CATALOGUE, "data_designer"),), entry_points=()
    )
    monkeypatch.setattr(registry_module, "current", lambda: reg)
    monkeypatch.setattr(operator_port, "_registry", reg)
    return reg


def _url(reg: OperatorRegistry) -> str:
    return f"/api/v1/operators/dd_expression/{reg.current_version('dd_expression')}/validate"


async def test_a_refused_template_is_422_naming_the_rule(
    client: httpx.AsyncClient, dd_registry: OperatorRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = Worker(FORBIDDEN)
    monkeypatch.setattr(validation, "send_validate", worker)
    params = {"expr": "{% set x = 1 %}{{ x }}"}
    response = await client.post(
        _url(dd_registry), json={"params": params, "columns": ["text", "uid", "_dw_row_key"]}
    )
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "template_invalid"
    assert "forbids {% set %}" in error["message"]
    assert error["details"]["errors"][0]["rule"] == "forbidden_tag"
    (payload,) = worker.payloads
    assert payload["column_class"] == "ExpressionColumnConfig"
    assert payload["params"] == params
    assert payload["columns"] == ["text", "uid"]  # system columns are never visible


async def test_a_valid_template_says_who_checked_it(
    client: httpx.AsyncClient, dd_registry: OperatorRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = Worker(VALID)
    monkeypatch.setattr(validation, "send_validate", worker)
    response = await client.post(_url(dd_registry), json={"params": {"expr": "{{ text }}"}})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["valid"] is True and body["template_rules"] == "checked by the Data Designer worker"
    assert body["references"] == {"expr": ["text"]}
    assert len(worker.payloads) == 1 and worker.payloads[0]["columns"] is None


async def test_a_silent_worker_is_503_never_valid(
    client: httpx.AsyncClient, dd_registry: OperatorRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(validation, "send_validate", Worker(CeleryTimeout("no answer")))
    response = await client.post(_url(dd_registry), json={"params": {"expr": "{{ text }}"}})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "designer_unavailable"
    assert "NOT checked" in response.json()["error"]["message"]


async def test_a_schema_invalid_param_never_reaches_the_worker(
    client: httpx.AsyncClient, dd_registry: OperatorRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = Worker(VALID)
    monkeypatch.setattr(validation, "send_validate", worker)
    response = await client.post(_url(dd_registry), json={"params": {"expr": 5}})
    assert response.status_code == 422 and worker.payloads == []


async def test_recipe_validation_lists_the_rule_on_the_dd_step(
    client: httpx.AsyncClient, dd_registry: OperatorRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = Worker(FORBIDDEN)
    monkeypatch.setattr(validation, "send_validate", worker)
    version = dd_registry.current_version("dd_expression")
    body = {
        "format": "dw.recipe/v1",
        "steps": [
            {
                "operator": "dd_expression",
                "version": version,
                "params": {"expr": "{% set x = 1 %}{{ x }}"},
            }
        ],
    }
    response = await client.post("/api/v1/recipes/validate", json={"body": body})
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["valid"] is False
    (step,) = out["steps"]
    assert [e["code"] for e in step["errors"]] == ["template_invalid"]
    assert "forbids {% set %}" in step["errors"][0]["message"]
    assert len(worker.payloads) == 1


async def test_recipe_validation_with_a_silent_worker_is_not_valid(
    client: httpx.AsyncClient, dd_registry: OperatorRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(validation, "send_validate", Worker(CeleryTimeout("x")))
    version = dd_registry.current_version("dd_expression")
    body = {
        "format": "dw.recipe/v1",
        "steps": [
            {"operator": "dd_expression", "version": version, "params": {"expr": "{{ text }}"}}
        ],
    }
    out = (await client.post("/api/v1/recipes/validate", json={"body": body})).json()
    assert out["valid"] is False
    assert out["steps"][0]["errors"][0]["code"] == "designer_unavailable"


# --- DLP run G2/G4/G7 (2026-10-21): every template against the columns its step will see -----

TOO_DEEP = {
    "valid": False,
    "errors": [
        {"field": "expr", "rule": "too_deep", "message": "nests 13 deep", "found": 13, "limit": 10}
    ],
    "references": {"expr": ["a"]},
    "limits": {"max_nodes": 600, "max_depth": 10, "filters": ["upper"]},
}


def _dd_body(version: str, *exprs: tuple[str, str]) -> dict[str, Any]:
    return {
        "format": "dw.recipe/v1",
        "steps": [
            {
                "operator": "dd_expression",
                "version": version,
                "params": {"expr": expr, "output_column": out},
            }
            for out, expr in exprs
        ],
    }


async def test_with_columns_each_template_is_checked_against_what_its_step_sees(
    client: httpx.AsyncClient, dd_registry: OperatorRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = Worker(VALID)
    monkeypatch.setattr(validation, "send_validate", worker)
    version = str(dd_registry.current_version("dd_expression"))
    body = _dd_body(version, ("a", "{{ text }}"), ("b", "{{ a }}"))
    response = await client.post(
        "/api/v1/recipes/validate", json={"body": body, "columns": ["text", "idx"]}
    )
    out = response.json()
    assert out["valid"] is True, out
    assert out["columns_checked"] == "given"
    assert [p["columns"] for p in worker.payloads] == [["idx", "text"], ["a", "idx", "text"]]
    assert out["limits"]["max_steps"] == 64 and out["limits"]["designer_max_depth"] == 10


async def test_a_failed_template_names_its_rule(
    client: httpx.AsyncClient, dd_registry: OperatorRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(validation, "send_validate", Worker(TOO_DEEP))
    version = str(dd_registry.current_version("dd_expression"))
    out = (
        await client.post(
            "/api/v1/recipes/validate",
            json={"body": _dd_body(version, ("a", "{{ text }}")), "columns": ["text"]},
        )
    ).json()
    (error,) = out["steps"][0]["errors"]
    assert (error["code"], error["rule"]) == ("template_invalid", "too_deep")


async def test_the_step_limit_is_stated_and_named(
    client: httpx.AsyncClient, dd_registry: OperatorRegistry
) -> None:
    steps = [{"operator": "drop_columns", "version": "1", "params": {"columns": ["x"]}}] * 65
    out = (
        await client.post(
            "/api/v1/recipes/validate", json={"body": {"format": "dw.recipe/v1", "steps": steps}}
        )
    ).json()
    assert out["valid"] is False
    (error,) = out["body_errors"]
    assert (error["code"], error["rule"]) == ("too_many_steps", "max_steps")
    assert out["limits"] == {"max_steps": 64}


async def test_a_native_steps_parameter_columns_are_checked(
    client: httpx.AsyncClient, dd_registry: OperatorRegistry
) -> None:
    body = {
        "format": "dw.recipe/v1",
        "steps": [
            {
                "operator": "checksum_validate",
                "version": "1",
                "params": {"column": "value", "scheme": "card_pan"},
            },
            {"operator": "drop_columns", "version": "1", "params": {"patterns": ["cv_*"]}},
            {
                "operator": "rule_label",
                "version": "1",
                "params": {"rules": ["x if cv_tier == low"]},
            },
        ],
    }
    out = (
        await client.post("/api/v1/recipes/validate", json={"body": body, "columns": ["text"]})
    ).json()
    errors = {s["index"]: [e["code"] for e in s["errors"]] for s in out["steps"]}
    # step 1 reads "value" (not there); step 3 reads cv_tier, which step 2 removed
    assert errors == {1: ["missing_input_column"], 2: [], 3: ["missing_input_column"]}


async def test_validate_by_reference_and_save_without_echo(
    client: httpx.AsyncClient, dd_registry: OperatorRegistry, operator_name: str
) -> None:
    body = {
        "format": "dw.recipe/v1",
        "steps": [
            {
                "operator": "regex_match",
                "version": "1",
                "params": {"column": "text", "pattern": "a+"},
            }
        ],
    }
    saved = await client.post("/api/v1/recipes?view=summary", json={"name": "rx", "body": body})
    assert saved.status_code == 201, saved.text
    summary = saved.json()
    assert "revisions" not in summary and "body" not in summary
    assert summary["step_count"] == 1 and summary["operators"] == {"regex_match@1": 1}
    assert len(summary["recipe_hash"]) == 64
    out = (
        await client.post(
            "/api/v1/recipes/validate",
            json={"recipe_revision_id": summary["revision_id"], "columns": ["text"]},
        )
    ).json()
    assert out["valid"] is True, out
    # an include block reuses a saved revision's steps; the stored body is the expanded one
    included = await client.post(
        "/api/v1/recipes?view=summary",
        json={
            "name": "rx2",
            "body": {
                "format": "dw.recipe/v1",
                "steps": [
                    {"include": summary["revision_id"]},
                    {
                        "operator": "drop_columns",
                        "version": "1",
                        "params": {"columns": ["rx_spans"]},
                    },
                ],
            },
        },
    )
    assert included.status_code == 201, included.text
    assert included.json()["operators"] == {"regex_match@1": 1, "drop_columns@1": 1}
    both = await client.post(
        "/api/v1/recipes/validate",
        json={"body": body, "recipe_revision_id": summary["revision_id"]},
    )
    assert both.status_code == 422 and both.json()["error"]["code"] == "recipe_body_ambiguous"


async def test_plan_build_checks_each_template_against_the_real_inputs(
    client: httpx.AsyncClient,
    dd_registry: OperatorRegistry,
    monkeypatch: pytest.MonkeyPatch,
    data_dir: Any,
    operator_name: str,
) -> None:
    """DLP run G2: plan_build said "valid" and the build then failed on depth 13 > 10. The plan
    now asks the designer worker about every template with the columns its step will see."""
    from tests.integration.test_version_build import setup, src
    from tests.support.version_fixtures import HUMOR_TRAIN, make_source

    worker = Worker(TOO_DEEP)
    monkeypatch.setattr(validation, "send_validate", worker)
    version = str(dd_registry.current_version("dd_expression"))
    source = make_source(data_dir, {"train": HUMOR_TRAIN})
    ds, rev = await setup(client, _dd_body(version, ("a", "{{ text }}")))
    response = await client.post(
        "/api/v1/versions/plan",
        json={"dataset_id": ds, "recipe_revision_id": rev, "inputs": [src(source)]},
    )
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["valid"] is False
    assert out["validation"]["columns_checked"] == "inputs"
    (error,) = out["validation"]["steps"][0]["errors"]
    assert error["rule"] == "too_deep"
    assert [p["columns"] for p in worker.payloads] == [["label", "note", "score", "text"]]
