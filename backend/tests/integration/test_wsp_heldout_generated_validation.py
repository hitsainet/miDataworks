"""MCP gap G-c (2026-11-25): validation reports finalize's P43 refusal before a build.

Finalize refuses a generated row in a ``wsp_heldout`` split (``wsp_heldout_contains_generated``,
check 6, 021-D26). ``dataworks_validate_recipe`` passed such a body, so FU-7a would have met the
refusal only after a full build. Each case here is checked three ways, through the PRODUCTION MCP
registry over the in-process app on a real PostgreSQL: ``dataworks_validate_recipe`` (the steps
alone), ``dataworks_plan_build`` (the steps and the inputs' rows) and the REAL build, so the
validation's answer is pinned to what finalize then does, case by case, in both directions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp_types import CallToolResult

from src.services import wsp_partitions
from tests.integration.test_directed_held_out import ECHO, KEEP, ROLES, carriers, driver, run
from tests.integration.test_version_build import request, setup, src
from tests.integration.test_wsp_versions import ALPHA_SPLIT, DIRECTED_WSP, alpha_rows, child_of
from tests.support.mcp_live import call, production_mcp
from tests.support.operator_build_driver import RealDriver
from tests.support.version_fixtures import make_source

__all__ = ["driver"]  # the fixture, imported for its registry of fixture operators

CODE = "wsp_heldout_contains_generated"
MARKS = {
    "wsp_roles": ["wsp_practice", "none", "wsp_heldout", "none", "none"],
    "wsp_set_kinds": ["in_distribution", "none", "hard_negatives", "none", "none"],
    "wsp_min_chars": [0, 0, 0, 0, 0],
    "wsp_text_column": "text",
}
REASSIGN_WSP = {
    "operator": "split",
    "version": "1.3.0",
    "params": {
        "split_names": ["train", "test"],
        "split_fractions": [0.5, 0.5],
        "held_out": ["test"],
        "mode": "reassign",
        "wsp_roles": ["wsp_practice", "wsp_heldout"],
        "wsp_set_kinds": ["in_distribution", "in_distribution"],
        "wsp_min_chars": [0, 0],
        "wsp_text_column": "text",
    },
}


def marked_directed() -> dict[str, Any]:
    step = json.loads(json.dumps(DIRECTED_WSP))
    step["params"].update(MARKS)
    return step


def value(result: Any) -> dict[str, Any]:
    """A tool's answer: its structured content when it publishes a schema, else the dict."""
    answer = result.structured_content if isinstance(result, CallToolResult) else result
    assert isinstance(answer, dict), answer
    return answer


def warned(answer: dict[str, Any]) -> dict[int, list[str]]:
    """``{step index: [codes]}`` of the steps that carry a warning."""
    return {
        s["index"]: [w["code"] for w in s.get("warnings", [])]
        for s in answer["steps"]
        if s.get("warnings")
    }


def failing(answer: dict[str, Any]) -> dict[int, list[str]]:
    """``{step index: [codes]}`` of the steps that carry an error."""
    return {s["index"]: [e["code"] for e in s["errors"]] for s in answer["steps"] if s["errors"]}


async def three_ways(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    driver: RealDriver,
    steps: list[dict[str, Any]],
    inputs: list[dict[str, Any]],
    name: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """(validate answer, plan answer, build outcome) for one recipe over ``inputs``."""
    mcp, _ = production_mcp(monkeypatch)
    body = {"format": "dw.recipe/v1", "steps": steps}
    validated = value(await call(mcp, "dataworks_validate_recipe", {"body": body}))
    ds, rev = await setup(client, body, name=name, target="detector")
    planned = value(
        await call(
            mcp,
            "dataworks_plan_build",
            {"dataset_id": ds, "recipe_revision_id": rev, "inputs": inputs, "column_roles": ROLES},
        )
    )
    response = await request(client, ds, rev, inputs, seed=7, column_roles=ROLES)
    assert response.status_code == 202, response.text
    from src.core.database import sync_session_factory
    from src.models import Job

    job_id = response.json()["job_id"]
    status = driver.run(job_id)
    with sync_session_factory()() as db:
        job = db.get(Job, job_id)
        assert job is not None and job.result is not None
        built = {"status": status, **job.result}
    return validated, planned, built


async def test_a_generator_before_a_marked_directed_split(
    client: httpx.AsyncClient,
    driver: RealDriver,
    data_dir: Path,
    operator_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FU-7a's shape: a generator before a value-directed split that marks a held-out split. Rows
    MAY land there (by their values), so validation and the plan WARN on step 2 with finalize's
    code and keep the recipe valid (operator decision, 2026-11-25); here some do, and the build is
    refused by that code."""
    source = make_source(data_dir, {"train": carriers("g")}, repo_id="t/g-directed")
    validated, planned, built = await three_ways(
        client, monkeypatch, driver, [ECHO, marked_directed()], [src(source)], "g-directed"
    )
    assert built["status"] == "failed" and built["error"]["code"] == CODE, built
    assert validated["valid"] is True and failing(validated) == {}
    assert warned(validated) == {2: [CODE]}
    assert validated["wsp_heldout_checked"] == "steps"
    assert planned["valid"] is True and failing(planned["validation"]) == {}
    assert warned(planned["validation"]) == {2: [CODE]}
    assert planned["validation"]["wsp_heldout_checked"] == "steps_and_inputs"
    warning = validated["steps"][1]["warnings"][0]
    assert warning["rule"] == "P43" and "'ood_genre'" in warning["message"]
    assert f"finalize refuses the build with {CODE}" in warning["message"]
    assert "If any generated row lands there" in warning["message"]


async def test_the_same_split_unmarked_passes_all_three(
    client: httpx.AsyncClient,
    driver: RealDriver,
    data_dir: Path,
    operator_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The control: FU-7a's recommendation (a) leaves the generated split unmarked."""
    source = make_source(data_dir, {"train": carriers("u")}, repo_id="t/u-directed")
    validated, planned, built = await three_ways(
        client, monkeypatch, driver, [ECHO, DIRECTED_WSP], [src(source)], "u-directed"
    )
    assert built["status"] == "completed", built
    assert validated["valid"] is True and validated["wsp_heldout_checked"] == "none"
    assert planned["valid"] is True and planned["validation"]["wsp_heldout_checked"] == "none"


async def test_a_generator_after_a_marked_split(
    client: httpx.AsyncClient,
    driver: RealDriver,
    data_dir: Path,
    operator_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An added row keeps its parent's split (the echo), so a generator after the split reaches
    the marked splits; validation names the generator's step."""
    source = make_source(data_dir, alpha_rows("after"), repo_id="t/after")
    validated, planned, built = await three_ways(
        client, monkeypatch, driver, [ALPHA_SPLIT, ECHO], [src(source)], "after"
    )
    assert built["status"] == "failed" and built["error"]["code"] == CODE, built
    assert failing(validated) == {2: [CODE]} and validated["valid"] is False
    assert failing(planned["validation"]) == {2: [CODE]} and planned["valid"] is False
    assert warned(validated) == {} and warned(planned["validation"]) == {}


async def test_source_rows_in_a_marked_split_pass_all_three(
    client: httpx.AsyncClient,
    driver: RealDriver,
    data_dir: Path,
    operator_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The control for P43's other half: source rows only (FU-7a's everyday sets)."""
    source = make_source(data_dir, alpha_rows("plain"), repo_id="t/plain")
    validated, planned, built = await three_ways(
        client, monkeypatch, driver, [ALPHA_SPLIT], [src(source)], "plain"
    )
    assert built["status"] == "completed", built
    assert validated["valid"] is True and validated["wsp_heldout_checked"] == "steps"
    assert planned["valid"] is True
    assert planned["validation"]["wsp_heldout_checked"] == "steps_and_inputs"


async def test_an_input_holding_generated_rows(
    client: httpx.AsyncClient,
    driver: RealDriver,
    data_dir: Path,
    operator_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FU-7a's pass 2: the recipe adds nothing, its input version holds the generated rows. Only
    the plan sees the inputs, so it reports what the steps alone cannot; validation says it
    checked the steps only."""
    source = make_source(data_dir, {"train": carriers("p")}, repo_id="t/pass1")
    parent = await run(client, driver, [ECHO], [src(source)], "pass1")
    assert parent["status"] == "completed", parent
    validated, planned, built = await three_ways(
        client,
        monkeypatch,
        driver,
        [KEEP, marked_directed()],
        [child_of(parent["version_id"])],
        "pass2",
    )
    assert built["status"] == "failed" and built["error"]["code"] == CODE, built
    assert validated["valid"] is True and validated["wsp_heldout_checked"] == "steps"
    assert planned["valid"] is False and failing(planned["validation"]) == {2: [CODE]}
    assert warned(planned["validation"]) == {}
    message = planned["validation"]["steps"][1]["errors"][0]["message"]
    assert f"input version {parent['version_id']} split 'train'" in message


INPUT = [{"version_id": "v1", "split": "test", "sample_keys": ["k"]}]


@pytest.mark.parametrize(
    ("steps", "inputs", "expected"),
    [
        # keep_source keeps each row's split: only an input's split of the marked name is reached
        ([("selector", {**ALPHA_SPLIT["params"]})], INPUT, []),
        (
            [("selector", {**REASSIGN_WSP["params"], "mode": "keep_source"})],
            INPUT,
            [(1, "error")],
        ),
        # a generator before a keep_source split keeps its seed's split: not reached
        ([("generator", {}), ("selector", {**ALPHA_SPLIT["params"]})], None, []),
        # after a split that reassigned a generator's rows, they may be anywhere: a warning
        (
            [
                ("generator", {}),
                ("selector", {"split_names": ["a", "b"], "mode": "reassign"}),
                ("selector", {**ALPHA_SPLIT["params"]}),
            ],
            None,
            [(3, "warning")],
        ),
        # ... and an input's generated rows reassigned: certain, an error
        (
            [
                ("selector", {"split_names": ["a", "b"], "mode": "reassign"}),
                ("selector", {**ALPHA_SPLIT["params"]}),
            ],
            INPUT,
            [(2, "error")],
        ),
        # an input holding generated rows reaching a reassigning marked split: an error
        ([("selector", {**REASSIGN_WSP["params"]})], INPUT, [(1, "error")]),
        # a generator before a reassigning marked split: may land there, a warning
        ([("generator", {}), ("selector", {**REASSIGN_WSP["params"]})], None, [(2, "warning")]),
        # both: the input makes it certain
        ([("generator", {}), ("selector", {**REASSIGN_WSP["params"]})], INPUT, [(2, "error")]),
        # a generator after the marked split: an error at the generator
        ([("selector", {**REASSIGN_WSP["params"]}), ("generator", {})], None, [(2, "error")]),
        ([("selector", {**REASSIGN_WSP["params"]})], None, []),
        ([("mapper", {}), ("selector", {**REASSIGN_WSP["params"]})], None, []),
    ],
    ids=[
        "keep-other",
        "keep-same",
        "gen-keep",
        "gen-reassign-keep",
        "input-reassign-keep",
        "input-reassign",
        "gen-reassign",
        "gen-and-input",
        "gen-after",
        "none",
        "mapper",
    ],
)
def test_where_generated_rows_can_reach(
    steps: list[tuple[str, dict[str, Any]]], inputs: Any, expected: list[tuple[int, str]]
) -> None:
    facts = [(i, kind, params) for i, (kind, params) in enumerate(steps, start=1)]
    found = wsp_partitions.generated_heldout_problems(facts, inputs)
    assert [(index, severity) for index, severity, _ in found] == expected


def test_finalize_and_validation_share_one_code() -> None:
    assert wsp_partitions.GENERATED_IN_HELDOUT == CODE


def _two_steps(first: str, second: str) -> Any:
    from src.schemas.recipes import StepValidation, ValidationOut

    return ValidationOut(
        valid=True,
        steps=[
            StepValidation(
                index=1, operator=first, version="1" if first != "split" else "1.3.0", errors=[]
            ),
            StepValidation(
                index=2, operator=second, version="1" if second != "split" else "1.3.0", errors=[]
            ),
        ],
    )


def test_the_report_itself_fails_validation(driver: RealDriver) -> None:
    """Both routes recompute ``valid`` after the designer check, so this pins the report's own
    verdict on a certain case (a generator after the marked split): a caller without that
    recomputation must not read ``valid: true`` (control C7)."""
    from src.services import recipe_service

    body = {"format": "dw.recipe/v1", "steps": [marked_directed(), ECHO]}
    got = recipe_service.report_generated_heldout(
        _two_steps("split", "fx_generator_echo"), body, None
    )
    assert got.valid is False and got.wsp_heldout_checked == "steps"
    assert [e.code for e in got.steps[1].errors] == [CODE] and got.steps[0].errors == []
    assert got.steps[1].warnings == []


def test_a_possible_reach_warns_and_stays_valid(driver: RealDriver) -> None:
    """The possible case (a generator before a reassigning marked split) is a step warning: the
    recipe stays valid and nothing lands in errors (operator decision, 2026-11-25; control W1)."""
    from src.services import recipe_service

    body = {"format": "dw.recipe/v1", "steps": [ECHO, marked_directed()]}
    got = recipe_service.report_generated_heldout(
        _two_steps("fx_generator_echo", "split"), body, None
    )
    assert got.valid is True and got.wsp_heldout_checked == "steps"
    assert got.steps[1].errors == [] and [w.code for w in got.steps[1].warnings] == [CODE]
    assert got.steps[1].warnings[0].rule == "P43"
