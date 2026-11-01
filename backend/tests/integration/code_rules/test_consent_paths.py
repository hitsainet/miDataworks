"""Consent paths around code rules that no earlier test held (018 Review R3).

- **The resolution parameter is the build's alone.** ``code_rule_resolution`` carries the code-rule
  documents ``rule_set@1`` runs. A recipe or an operator preview that sets it is refused
  (``rule_set_document_reserved``) before anything runs: otherwise a preview could hand the operator
  the genuine document of a REVOKED hash (it hashes to the signal) and run it on the runner.
- **A revocation between a step's dispatch and its ingest fails THAT step** (FTID X23): the ingest
  pass resolves the step again, reads the approval fresh, and nothing is completed or recorded.
- **A revocation written while a save's runner checks run is never undone by that save** (ENH-006
  D60): the save re-reads the state before it writes, and a revoked hash is refused, so no
  ``approve`` row follows the operator's ``revoke`` without "approve again".
- **The switch off still honours approvals** (FTID X25): a build of a rule set naming a revoked hash
  is refused at load with ``CODE_RULES_ENABLED`` off, never run as "unavailable".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.api.v1.endpoints import operators as operators_endpoint
from src.core.database import get_sync_engine
from src.operators import preview as preview_module
from src.runner_contract.hashing import canonical_document_bytes, code_sha256
from tests.integration.code_rules.conftest import count, revoke, save
from tests.integration.code_rules.test_build_dispatch import (
    finished,
    ready,
    recipe,
    rs_step,
    rule_set_executions,
    start,
    texts_for,
)
from tests.support import code_rule_fixtures as fx
from tests.support.fake_runner import FakeRunner
from tests.support.version_fixtures import make_source

pytestmark = pytest.mark.usefixtures("operator_name")


def _revoke_now(sha: str, reason: str) -> None:
    """An operator revocation written straight to the store (mid-flight, from 'another tab')."""
    with get_sync_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO dw_code_rule_approvals (code_sha256, action, reason, changed_by, "
                "origin) VALUES (:s, 'revoke', :r, 'Ada', 'operator')"
            ),
            {"s": sha, "r": reason},
        )


def _actions(sha: str) -> list[str]:
    with get_sync_engine().connect() as conn:
        return [
            str(a)
            for a in conn.execute(
                text(
                    "SELECT action FROM dw_code_rule_approvals WHERE code_sha256 = :s ORDER BY id"
                ),
                {"s": sha},
            ).scalars()
        ]


async def test_an_operator_preview_cannot_hand_rule_set_its_own_code_rule_documents(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    data_dir: Path,
    behaviour: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rule, rs = await ready(client, behaviour)
    await revoke(client, rule["code_sha256"])
    sent: list[dict[str, Any]] = []

    def run(req: dict[str, Any], entry: Any) -> None:
        sent.append(req)
        preview_module.run_request(req)

    monkeypatch.setattr(operators_endpoint, "send_preview", run)
    document = fx.save_body(behaviour)["document"]
    canonical = canonical_document_bytes(document).decode("utf-8")
    assert code_sha256(document) == rule["code_sha256"]  # the genuine document of the revoked hash
    injected = json.dumps(
        {
            "approvals": {},
            "documents": {rule["code_sha256"]: canonical},
            "runner_limits": {},
            "versions": {},
        }
    )
    source = make_source(data_dir, {"train": [{"text": t} for t in texts_for(behaviour)]})
    stored_runs = len(fake.runs("stored"))
    response = await client.post(
        "/api/v1/operators/rule_set/1/preview",
        json={
            "params": {**rs_step(rs)["params"], "code_rule_resolution": injected},
            "input": {"source_id": source},
            "seed": 3,
        },
    )
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "rule_set_document_reserved", error
    assert error["details"]["param"] == "code_rule_resolution"
    assert sent == [] and len(fake.runs("stored")) == stored_runs, "the revoked rule ran"


@pytest.mark.usefixtures("production_driver")
async def test_a_revocation_between_dispatch_and_ingest_fails_that_step(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    data_dir: Path,
    behaviour: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rule, rs = await ready(client, behaviour)
    from src.operators.native.rules import rule_set_op

    real_run = rule_set_op.RuleSetOp.run
    revoked: list[bool] = []

    def run_then_revoke(self: Any, batch: Any, params: Any, ctx: Any) -> Any:
        out = real_run(self, batch, params, ctx)
        if not revoked:  # dispatched approved; revoked before the build ingests the step
            _revoke_now(rule["code_sha256"], "between dispatch and ingest")
            revoked.append(True)
        return out

    monkeypatch.setattr(rule_set_op.RuleSetOp, "run", run_then_revoke)
    job, _ = await start(
        client, data_dir, recipe(rs_step(rs)), texts_for(behaviour), f"{behaviour}-x23"
    )
    production_driver.run(job)
    assert revoked, "the operator never ran"
    status, result = finished(job)
    assert status == "failed" and result is not None, (status, result)
    assert result["error"]["code"] == "code_rule_not_approved", result["error"]
    assert result["error"]["details"]["state"] == "revoked"
    assert [e.state for e in rule_set_executions()] != ["completed"]
    assert count("dw_code_rule_uses") == 0
    assert count("dw_versions") == 0


async def test_a_revocation_during_a_saves_runner_checks_is_never_undone_by_the_save(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    behaviour: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = fx.save_body(behaviour)
    assert (await save(client, body)).status_code == 201  # v1, approved by the operator's save
    sha = code_sha256(body["document"])
    from src.services.code_rules import save as save_module

    real_inspect = save_module._inspect

    async def inspect_then_revoke(client_: Any, parsed: Any) -> Any:
        out = await real_inspect(client_, parsed)
        _revoke_now(sha, "revoked while the save was checking")
        return out

    monkeypatch.setattr(save_module, "_inspect", inspect_then_revoke)
    versions = count("dw_config_versions", "kind = 'code_rule'")
    edited = fx.save_body(behaviour)
    edited["cases"] = list(reversed(edited["cases"]))  # a case-only edit: the same hash
    refused = await save(client, edited)
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "code_rule_revoked"
    assert _actions(sha) == ["approve", "revoke"], "the save undid the revocation"
    assert count("dw_config_versions", "kind = 'code_rule'") == versions


@pytest.mark.usefixtures("production_driver")
async def test_with_the_switch_off_a_revoked_hash_still_refuses_the_build(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    data_dir: Path,
    behaviour: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.core.config import get_settings

    rule, rs = await ready(client, behaviour)
    await revoke(client, rule["code_sha256"])
    monkeypatch.setattr(get_settings(), "code_rules_enabled", False)
    job, _ = await start(
        client, data_dir, recipe(rs_step(rs)), texts_for(behaviour), f"{behaviour}-offrev"
    )
    production_driver.run(job)
    status, result = finished(job)
    assert status == "failed" and result is not None
    assert result["error"]["code"] == "code_rule_not_approved", result["error"]
    assert result["error"]["details"]["state"] == "revoked"
    assert not [d for d in production_driver.dispatched if d["operator"] == "rule_set"]


@pytest.mark.usefixtures("production_driver")
async def test_a_build_runs_the_rule_sets_own_cases_with_the_runners_values_at_load(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    data_dir: Path,
    behaviour: str,
) -> None:
    """FR-018.37: the cases run through the runner at save AND at build load. The rule answers
    differently now (the runner's values contradict a stored case): the build refuses at load,
    naming the case, and never dispatches the step."""
    rule, rs = await ready(client, behaviour)
    function = fx.RULE_SET_SHAPES[behaviour]["function"]
    fake.twins[rule["code_sha256"]] = {function: lambda text, params: 0}
    job, _ = await start(
        client, data_dir, recipe(rs_step(rs)), texts_for(behaviour), f"{behaviour}-load-cases"
    )
    production_driver.run(job)
    status, result = finished(job)
    assert status == "failed" and result is not None
    assert result["error"]["code"] == "case_failed", result["error"]
    assert result["error"]["details"]["case"] == "both"
    assert not [d for d in production_driver.dispatched if d["operator"] == "rule_set"]


async def test_approve_again_on_a_runner_without_a_free_slot_is_503_and_writes_nothing(
    client: httpx.AsyncClient, fake: FakeRunner, behaviour: str
) -> None:
    """D61 with the runner's lane backstop: a starved re-run decides nothing about the rule - it
    is the runner's fault (503), never "a case fails now" (409), and nothing is approved."""
    body = fx.save_body(behaviour)
    assert (await save(client, body)).status_code == 201
    sha = code_sha256(body["document"])
    await revoke(client, sha)
    fake.starve_after = 1
    refused = await client.post(
        f"/api/v1/code-rules/approvals/{sha}/approve", json={"reason": "narrowed"}
    )
    assert refused.status_code == 503, refused.text
    assert refused.json()["error"]["code"] == "runner_unavailable"
    assert _actions(sha) == ["approve", "revoke"]


@pytest.mark.usefixtures("production_driver")
async def test_a_row_the_runner_timed_out_never_fails_the_step_and_blocks_its_reuse(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    data_dir: Path,
    behaviour: str,
) -> None:
    """D36 I7/I8 for a host fault the runner REPORTS per row (``rule_timeout``, D63), not a dead
    runner: the row's columns are null with the reason, the share ignores it (one row in six is
    far above the 1% share), and the step is never reused."""
    _, rs = await ready(client, behaviour)
    texts = texts_for(behaviour)
    fake.fail_texts = {texts[0]: "rule_timeout"}
    job, source = await start(client, data_dir, recipe(rs_step(rs)), texts, f"{behaviour}-timeout")
    production_driver.run(job)
    status, result = finished(job)
    assert status == "completed", result
    (execution,) = rule_set_executions()
    assert execution.reuse_blocked is True, "a timed-out row was cached as the rule's answer"
    fake.fail_texts = {}
    again, _ = await start(client, data_dir, recipe(rs_step(rs)), [], f"{behaviour}-t2", source)
    production_driver.run(again)
    assert finished(again)[0] == "completed"
    assert len(rule_set_executions()) == 2, "the blocked step was reused"
