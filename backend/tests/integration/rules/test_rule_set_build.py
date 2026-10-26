"""``rule_set@1`` in real builds: resolution, use records, pinning, the load-time case re-check
(FR-016.13, FR-016.22 – FR-016.25; FTID 016 sections 3.5 – 3.7).

Real PostgreSQL, the production operator registry, the real executor and ``advance_build``; only the
broker is replaced (``production_driver``). Both fixture behaviours.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy import text

from src.core.canonical_json import canonical_json
from src.core.database import get_sync_engine, sync_session_factory
from src.core.storage import resolve_under_data_dir
from src.models import Job, StepExecution, Version
from src.rules import rule_set
from src.rules.cases import case_input
from src.services import identity
from src.services.rules import step_resolution
from tests.integration.rules.conftest import RS, fixture_document, saved
from tests.integration.test_version_build import request, setup, src
from tests.support.operator_build_driver import RealDriver
from tests.support.version_fixtures import make_source

pytestmark = pytest.mark.usefixtures("operator_name", "production_driver")
ROLES = {"text": "content"}


def pin(out: dict[str, Any]) -> dict[str, Any]:
    return {
        "rule_set_name": out["name"],
        "rule_set_version": out["version"],
        "rule_set_sha256": out["sha256"],
    }


def rs_step(out: dict[str, Any], use: str = "label", **extra: Any) -> dict[str, Any]:
    params = {**pin(out), "use": use, "text_column": "text", **extra}
    return {"operator": "rule_set", "version": "1", "params": params}


def recipe(*steps: dict[str, Any]) -> dict[str, Any]:
    return {"format": "dw.recipe/v1", "steps": list(steps)}


def case_rows(document: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for case in document["cases"]:
        text_value, element = case_input(case)
        rows.append({"case": case["id"], "text": text_value, "element": element})
    return rows


async def build_rows(
    client: httpx.AsyncClient,
    driver: RealDriver,
    data_dir: Path,
    body: dict[str, Any],
    rows: list[dict[str, Any]],
    name: str,
    *,
    source: str | None = None,
) -> tuple[str, list[dict[str, Any]], str]:
    source = source or make_source(data_dir, {"train": rows}, repo_id=f"t/{name}")
    ds, rev = await setup(client, body, name=name)
    response = await request(client, ds, rev, [src(source)], seed=7, column_roles=ROLES)
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    status = driver.run(job_id)
    with sync_session_factory()() as db:
        job = db.get(Job, job_id)
        assert job is not None
        assert status == "completed", (job.error, job.result)
        version = db.get(Version, job.result["version_id"])  # type: ignore[index]
        assert version is not None
        paths = [s["path"] for s in version.splits]
        version_id = str(version.id)
    tables = [pq.read_table(resolve_under_data_dir(p)) for p in paths]
    rows_out = pa.concat_tables(tables).to_pylist() if tables else []
    return version_id, rows_out, source


def _uses() -> list[dict[str, Any]]:
    with get_sync_engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.name, c.number, u.rule_set_sha256, u.behaviour_key, u.use, "
                "u.step_execution_id FROM dw_rule_set_uses u JOIN dw_config_versions c "
                "ON c.id = u.config_version_id ORDER BY c.name"
            )
        ).all()
    return [
        {
            "name": r[0],
            "version": r[1],
            "sha256": r[2],
            "behaviour_key": r[3],
            "use": r[4],
            "execution": str(r[5]),
        }
        for r in rows
    ]


# --- 11.1: each fixture rule set writes its declared columns with the cases' values -------------


async def test_each_fixture_rule_set_writes_its_columns_with_the_cases_values(
    client: httpx.AsyncClient,
    production_driver: RealDriver,
    data_dir: Path,
    behaviour: str,
) -> None:
    document = fixture_document(behaviour)
    out = await saved(client, document)
    body = recipe(rs_step(out, element_column="element", output_prefix="x_"))
    _, rows, _ = await build_rows(
        client, production_driver, data_dir, body, case_rows(document), behaviour
    )
    by_case = {r["case"]: r for r in rows}
    assert sorted(by_case) == sorted(c["id"] for c in document["cases"])
    declared = ["x_" + o["column"] for o in document["outputs"]]
    if document.get("vetoes"):
        declared.append("x_vetoed")
    for case in document["cases"]:
        row = by_case[case["id"]]
        for column, value in case["expect"]["outputs"].items():
            assert row["x_" + column] == value, (case["id"], column)
        if document.get("vetoes"):
            assert row["x_vetoed"] is case["expect"]["vetoed"]
    written = {k for k in rows[0] if k.startswith("x_")}
    assert written == set(declared), "exactly the declared columns"


# --- 11.4 / 11.7: dispatch carries the stored text once per step; uses are recorded ------------


async def test_a_cross_behaviour_screen_records_both_uses_and_dispatches_the_stored_text(
    client: httpx.AsyncClient,
    production_driver: RealDriver,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alpha = await saved(client, fixture_document("fixture-alpha"))
    beta = await saved(client, fixture_document("fixture-beta"))
    dispatched: list[dict[str, Any]] = []
    reg = production_driver.registry
    original = reg.dispatch_step

    def spy(spec: Any, *args: Any, **kwargs: Any) -> Any:
        dispatched.append({"operator": spec.operator, "params": dict(spec.params)})
        return original(spec, *args, **kwargs)

    monkeypatch.setattr(reg, "dispatch_step", spy)
    body = recipe(
        rs_step(alpha, "label", element_column="element", output_prefix=""),
        rs_step(beta, "screen", output_prefix=""),
    )
    rows = case_rows(fixture_document("fixture-alpha")) + case_rows(
        fixture_document("fixture-beta")
    )
    version_id, built, _ = await build_rows(
        client, production_driver, data_dir, body, rows, "cross"
    )
    stored = {}
    with get_sync_engine().connect() as conn:
        for name, sha, canonical in conn.execute(
            text("SELECT name, body_sha256, body_canonical FROM dw_config_versions")
        ).all():
            stored[name] = (sha, canonical)
    rule_steps = [d for d in dispatched if d["operator"] == "rule_set"]
    assert [d["params"]["rule_set_name"] for d in rule_steps] == [alpha["name"], beta["name"]]
    for d in rule_steps:
        assert d["params"]["rule_set_document"] == stored[d["params"]["rule_set_name"]][1]
    uses = _uses()
    assert [
        {k: u[k] for k in ("name", "version", "sha256", "behaviour_key", "use")} for u in uses
    ] == [
        {
            "name": alpha["name"],
            "version": 1,
            "sha256": alpha["sha256"],
            "behaviour_key": "fixture-alpha",
            "use": "label",
        },
        {
            "name": beta["name"],
            "version": 1,
            "sha256": beta["sha256"],
            "behaviour_key": "fixture-beta",
            "use": "screen",
        },
    ]
    assert len({u["execution"] for u in uses}) == 2, "one row per step"
    # params_hash covers the RESOLVED parameters (the document text is part of the identity)
    with sync_session_factory()() as db:
        for u, d in zip(uses, rule_steps, strict=True):
            execution = db.get(StepExecution, u["execution"])
            assert execution is not None
            assert execution.params_hash == identity.params_hash(d["params"])
    got = (await client.get(f"{RS}/{alpha['name']}")).json()
    assert got["used_by"] == [
        {
            "version_id": version_id,
            "dataset_id": got["used_by"][0]["dataset_id"],
            "number": 1,
            "step_index": 1,
            "use": "label",
        }
    ]
    assert {r["fb_label"] for r in built} == {True, False}


async def test_an_empty_input_step_still_records_its_use(
    client: httpx.AsyncClient, production_driver: RealDriver, data_dir: Path
) -> None:
    beta = await saved(client, fixture_document("fixture-beta"))
    body = recipe(
        {
            "operator": "length_band",
            "version": "1.0.0",
            "params": {"column": "text", "min_length": 1_000_000, "max_length": 2_000_000},
        },
        rs_step(beta, "conflict_scan"),
    )
    _, rows, _ = await build_rows(
        client,
        production_driver,
        data_dir,
        body,
        case_rows(fixture_document("fixture-beta")),
        "empty",
    )
    assert rows == []
    assert [(u["name"], u["use"]) for u in _uses()] == [(beta["name"], "conflict_scan")]


# --- 11.9: a pinned recipe rebuilds identically after version 2 exists; the plan shows the move -


async def test_a_pinned_recipe_rebuilds_identically_and_the_plan_lists_the_move(
    client: httpx.AsyncClient, production_driver: RealDriver, data_dir: Path, behaviour: str
) -> None:
    document = fixture_document(behaviour)
    v1 = await saved(client, document)
    body = recipe(rs_step(v1, element_column="element"))
    rows = case_rows(document)
    first_id, first, source = await build_rows(
        client, production_driver, data_dir, body, rows, f"{behaviour}-pin"
    )
    edited = copy.deepcopy(document)
    edited["description"] = "Version two."
    v2 = await saved(client, edited)
    assert v2["version"] == 2
    second_id, second, _ = await build_rows(
        client, production_driver, data_dir, body, rows, f"{behaviour}-pin2", source=source
    )
    assert first == second
    assert {u["version"] for u in _uses()} == {1}
    plan = await client.post("/api/v1/operators/upgrade-plan", json={"recipe_body": body})
    assert plan.status_code == 200, plan.text
    (step,) = plan.json()["steps"]
    assert step["params_valid"] is True, step
    assert step["rule_set"]["from"] == {
        "name": document["name"],
        "version": 1,
        "sha256": v1["sha256"],
    }
    assert step["rule_set"]["to"] == {
        "name": document["name"],
        "version": 2,
        "sha256": v2["sha256"],
    }
    broken = recipe(
        {
            **rs_step(v1, element_column="element"),
            "params": {**rs_step(v1)["params"], "rule_set_sha256": "0" * 64},
        }
    )
    bad = (await client.post("/api/v1/operators/upgrade-plan", json={"recipe_body": broken})).json()
    (bad_step,) = bad["steps"]
    assert bad_step["params_valid"] is False
    assert [e.get("code") for e in bad_step["errors"]] == ["rule_set_hash_mismatch"]
    assert step["rule_set"]["difference"] == [
        {
            "op": "replace",
            "path": "/description",
            "old": document["description"],
            "new": "Version two.",
        }
    ]


# --- 11.3: recipe validation resolves at every call site ----------------------------------------


def _downstream(out: dict[str, Any], document: dict[str, Any]) -> dict[str, Any]:
    """A rule-set step and a step that READS one of its output columns: valid only when the
    rule-set step was resolved (its columns come from the stored document)."""
    column = "rs_" + document["outputs"][0]["column"]
    return recipe(
        rs_step(out, element_column="element"),
        {"operator": "drop_columns", "version": "1", "params": {"columns": [column]}},
    )


async def test_create_resolves_the_rule_set_step(client: httpx.AsyncClient, behaviour: str) -> None:
    document = fixture_document(behaviour)
    out = await saved(client, document)
    created = await client.post(
        "/api/v1/recipes", json={"name": "r", "body": _downstream(out, document)}
    )
    assert created.status_code == 201, created.text


async def test_revise_resolves_the_rule_set_step(client: httpx.AsyncClient) -> None:
    document = fixture_document("fixture-beta")
    out = await saved(client, document)
    created = await client.post("/api/v1/recipes", json={"name": "r", "body": recipe(rs_step(out))})
    assert created.status_code == 201, created.text
    revised = await client.post(
        f"/api/v1/recipes/{created.json()['id']}/revisions",
        json={"body": _downstream(out, document)},
    )
    assert revised.status_code == 201, revised.text


async def test_import_resolves_the_rule_set_step(client: httpx.AsyncClient) -> None:
    document = fixture_document("fixture-beta")
    out = await saved(client, document)
    body = _downstream(out, document)
    from src.services.identity import recipe_hash

    file_doc = {
        "format": "dw.recipe-file/v1",
        "name": "imported",
        "description": None,
        "body": body,
        "recipe_hash": recipe_hash(body),
    }
    from src.schemas.recipes import RECIPE_FILE_FORMAT

    file_doc["format"] = RECIPE_FILE_FORMAT
    raw = canonical_json(file_doc)
    answer = await client.post(
        "/api/v1/recipes/import", files={"file": ("r.json", io.BytesIO(raw), "application/json")}
    )
    assert answer.status_code in (200, 201), answer.text
    assert answer.json()["outcome"] == "created", answer.text
    assert answer.json()["reasons"] == []


async def test_validate_route_resolves_the_rule_set_step(client: httpx.AsyncClient) -> None:
    document = fixture_document("fixture-alpha")
    out = await saved(client, document)
    answer = await client.post(
        "/api/v1/recipes/validate",
        json={"body": _downstream(out, document), "columns": ["text", "element"]},
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["valid"] is True, answer.json()


async def test_build_request_and_plan_resolve_the_rule_set_step(
    client: httpx.AsyncClient, data_dir: Path
) -> None:
    document = fixture_document("fixture-alpha")
    out = await saved(client, document)
    source = make_source(data_dir, {"train": case_rows(document)}, repo_id="t/plan")
    ds, rev = await setup(client, _downstream(out, document), name="plan")
    plan = await client.post(
        "/api/v1/versions/plan",
        json={"dataset_id": ds, "recipe_revision_id": rev, "inputs": [src(source)]},
    )
    assert plan.status_code == 200, plan.text
    assert plan.json()["valid"] is True, plan.json()
    response = await request(client, ds, rev, [src(source)], seed=7, column_roles=ROLES)
    assert response.status_code == 202, response.text


async def test_validation_refuses_what_resolution_refuses(client: httpx.AsyncClient) -> None:
    document = fixture_document("fixture-beta")
    out = await saved(client, document)
    cases = {
        "rule_set_not_found": {**rs_step(out)["params"], "rule_set_version": 9},
        "rule_set_hash_mismatch": {**rs_step(out)["params"], "rule_set_sha256": "0" * 64},
        "rule_set_document_reserved": {**rs_step(out)["params"], "rule_set_document": "{}"},
    }
    for code, params in cases.items():
        answer = await client.post(
            "/api/v1/recipes/validate",
            json={"body": recipe({"operator": "rule_set", "version": "1", "params": params})},
        )
        assert answer.status_code == 200, answer.text
        (step,) = answer.json()["steps"]
        assert [e["code"] for e in step["errors"]] == [code], step


async def test_the_recipe_cap_names_the_setting(client: httpx.AsyncClient) -> None:
    from src.core import runtime_settings

    alpha = await saved(client, fixture_document("fixture-alpha"))
    beta = await saved(client, fixture_document("fixture-beta"))
    body = recipe(rs_step(alpha, output_prefix="a_"), rs_step(beta, output_prefix="b_"))
    runtime_settings.apply({"recipe_max_rule_sets": "1"})
    refused = (await client.post("/api/v1/recipes/validate", json={"body": body})).json()
    assert refused["valid"] is False
    assert [(e["code"], e["rule"]) for e in refused["body_errors"]] == [
        ("cap_exceeded", "RECIPE_MAX_RULE_SETS")
    ]
    runtime_settings.apply({"recipe_max_rule_sets": "2"})
    assert (await client.post("/api/v1/recipes/validate", json={"body": body})).json()["valid"]


# --- 11.5 / 11.6: the stored document's own cases are re-run; a raising validator fails the step -


def _store_raw(document: dict[str, Any]) -> dict[str, Any]:
    """Insert a version directly, bypassing save (as a row written before a validator changed)."""
    canonical = rule_set.canonical_bytes(document)
    sha = hashlib.sha256(canonical).hexdigest()
    with get_sync_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO dw_rule_sets (name, behaviour_key, created_by, created_by_origin) "
                "VALUES (:n, :k, 'Test Operator', 'operator') ON CONFLICT DO NOTHING"
            ),
            {"n": document["name"], "k": document["behaviour_key"]},
        )
        conn.execute(
            text(
                "INSERT INTO dw_config_versions (id, kind, name, number, plugin, body, "
                "body_sha256, body_canonical, created_by, created_by_origin) VALUES ('rsv_raw', "
                "'rule_set', :n, 1, 'rule_set', CAST(:b AS jsonb), :s, :c, 'Test Operator', "
                "'operator')"
            ),
            {"n": document["name"], "b": json.dumps(document), "s": sha, "c": canonical.decode()},
        )
    return {"name": document["name"], "version": 1, "sha256": sha}


async def test_a_stored_version_whose_case_fails_is_refused_naming_the_case(
    client: httpx.AsyncClient,
) -> None:
    document = fixture_document("fixture-beta")
    document["cases"][0]["expect"]["vetoed"] = True
    out = _store_raw(document)
    answer = await client.post("/api/v1/recipes/validate", json={"body": recipe(rs_step(out))})
    (step,) = answer.json()["steps"]
    assert [e["code"] for e in step["errors"]] == ["case_failed"]
    assert document["cases"][0]["id"] in step["errors"][0]["message"]


async def test_a_build_rechecks_the_cases_and_names_the_failing_one(
    client: httpx.AsyncClient,
    production_driver: RealDriver,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A validator that changed after the recipe was saved (here: card_pan reads nothing valid)
    is caught when the build resolves the step, naming the case; no row is labelled."""
    document = fixture_document("fixture-alpha")
    out = await saved(client, document)
    source = make_source(data_dir, {"train": case_rows(document)}, repo_id="t/recheck")
    ds, rev = await setup(client, recipe(rs_step(out, element_column="element")), name="re")
    response = await request(client, ds, rev, [src(source)], seed=7, column_roles=ROLES)
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    # the validator changes AFTER the request was accepted (a deploy between request and run)
    from src.rules.validators import VALIDATORS
    from src.rules.validators.base import Verdict

    step_resolution._recheck.cache_clear()
    validator = VALIDATORS[("card_pan", 1)]
    monkeypatch.setattr(
        validator,
        "check",
        lambda *a, **k: Verdict(False, "bad_check_digit", "none", "none", ""),
    )
    assert production_driver.run(job_id) == "failed"
    with sync_session_factory()() as db:
        job = db.get(Job, job_id)
        assert job is not None and job.result is not None
        error = job.result["error"]
    assert error["code"] == "case_failed", error
    assert error["details"]["case"] == "cued_element"
    assert _uses() == []


async def test_a_raising_validator_fails_the_step_naming_validator_and_row(
    client: httpx.AsyncClient,
    production_driver: RealDriver,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = fixture_document("fixture-alpha")
    out = await saved(client, document)
    source = make_source(data_dir, {"train": case_rows(document)}, repo_id="t/raise")
    ds, rev = await setup(client, recipe(rs_step(out, element_column="element")), name="ra")
    # Resolution re-runs the cases first: let it pass, then make the validator raise in the run.
    from src.operators.native.rules import rule_set_op
    from src.rules.validators import VALIDATORS

    validator = VALIDATORS[("card_pan", 1)]
    real_check = validator.check
    original_compiled = rule_set_op._compiled.__wrapped__

    def compiled_then_break(text_value: str, code_rules: bool = False) -> Any:
        result = original_compiled(text_value, code_rules)

        def boom(*a: Any, **k: Any) -> Any:
            raise RuntimeError("validator exploded")

        monkeypatch.setattr(validator, "check", boom)
        return result

    rule_set_op._compiled.cache_clear()
    monkeypatch.setattr(rule_set_op, "_compiled", compiled_then_break)
    response = await request(client, ds, rev, [src(source)], seed=7, column_roles=ROLES)
    job_id = response.json()["job_id"]
    assert production_driver.run(job_id) == "failed"
    monkeypatch.setattr(validator, "check", real_check)
    with sync_session_factory()() as db:
        job = db.get(Job, job_id)
        assert job is not None and job.result is not None
        error = job.result["error"]
    assert error["code"] == "rule_signal_error", error
    details = error["details"].get("executor_error", {}).get("details") or error["details"]
    assert details["validator"] == "card_pan@1" and details["signal"] == "card"
    assert len(details["row_key"]) > 0
    assert "4111" not in json.dumps(error)
