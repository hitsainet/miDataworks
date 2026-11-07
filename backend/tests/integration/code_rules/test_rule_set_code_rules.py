"""Rule sets naming code rules (feature 018 slice 4; FTASKS 13.3, 13.9; FR-018.37, FR-018.38, 018.16).

016's refusal ``code_rule_not_available`` is lifted: an APPROVED hash saves, and the save refuses,
naming the signal and a ``next``, everything else - the switch off, an unknown, pending or revoked
hash, a rule failing under the runner's contract minor (with the synchronous fallback when no row
exists yet), an undeclared function, params failing the rule's schema, ``within_n`` over it, and
any use that needs its positions. The rule set's own cases run with the runner's counts (the fake
runner runs the fixture's Python twin, never the source), so ``all`` / ``any`` / ``not`` evaluate to
the cases' outputs; a preview of a firing code rule shows ``spans: null``. Both fixture behaviours.
"""

from __future__ import annotations

import copy
from typing import Any

import httpx
import pytest

from tests.integration.code_rules.conftest import (
    AGENT,
    RS,
    approved_rule,
    count,
    revoke,
    save,
    saved_rule_set,
)
from tests.support import code_rule_fixtures as fx
from tests.support.fake_runner import FakeRunner

pytestmark = pytest.mark.usefixtures("operator_name")


async def _refused(
    client: httpx.AsyncClient, document: dict[str, Any], status: int, code: str, **named: Any
) -> dict[str, Any]:
    from tests.integration.code_rules.conftest import live_tools

    response = await client.post(RS, json={"document": document})
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code, error
    for key, value in named.items():
        assert error["details"].get(key) == value, (key, error["details"])
    nxt = error["details"].get("next")
    assert nxt is not None and nxt["tool"] in await live_tools(), error
    assert count("dw_config_versions", "kind = 'rule_set'") == 0
    details: dict[str, Any] = error["details"]
    return details


async def test_an_approved_hash_saves_and_its_cases_hold_with_the_runners_counts(
    client: httpx.AsyncClient, fake: FakeRunner, behaviour: str
) -> None:
    rule = await approved_rule(client, behaviour)
    out = await saved_rule_set(client, fx.rule_set_document(behaviour, rule["code_sha256"]))
    assert out["version"] == 1 and out["created"] is True
    # The cases ran through the runner (stored mode, the interactive lane, the hash by name).
    texts = [c["input"]["text"] for c in fx.rule_set_document(behaviour, "0" * 64)["cases"]]
    runs = [r for r in fake.runs("stored") if r["document"]["code_sha256"] == rule["code_sha256"]]
    assert len(runs) == 1, runs
    assert runs[0]["lane"] == "interactive"
    assert [c["texts"] for c in runs[0]["calls"]] == [texts]
    assert runs[0]["calls"][0]["function"] == fx.RULE_SET_SHAPES[behaviour]["function"]


async def test_the_switch_off_refuses_as_code_rules_disabled(
    client: httpx.AsyncClient, fake: FakeRunner, monkeypatch: pytest.MonkeyPatch, behaviour: str
) -> None:
    from src.core.config import get_settings

    rule = await approved_rule(client, behaviour)
    monkeypatch.setattr(get_settings(), "code_rules_enabled", False)
    await _refused(
        client,
        fx.rule_set_document(behaviour, rule["code_sha256"]),
        409,
        "code_rules_disabled",
        signal="coded",
    )


async def test_an_unknown_hash_is_refused_not_approved(
    client: httpx.AsyncClient, fake: FakeRunner, behaviour: str
) -> None:
    details = await _refused(
        client,
        fx.rule_set_document(behaviour, "a" * 64),
        422,
        "code_rule_not_approved",
        signal="coded",
        state="unknown",
    )
    assert details["code_sha256"] == "a" * 64


async def test_a_pending_hash_is_refused_not_approved(
    client: httpx.AsyncClient, fake: FakeRunner, behaviour: str
) -> None:
    pending = await save(client, fx.save_body(behaviour), agent=True)
    assert pending.status_code == 202, pending.text
    from src.runner_contract.hashing import code_sha256

    sha = code_sha256(fx.fixture(behaviour)["document"])
    await _refused(
        client,
        fx.rule_set_document(behaviour, sha),
        422,
        "code_rule_not_approved",
        state="pending",
    )


async def test_a_revoked_hash_is_refused_and_removing_the_approval_check_turns_it_green(
    client: httpx.AsyncClient, fake: FakeRunner, behaviour: str
) -> None:
    """FR-018.37's control target: ``check_signal``'s approval line (M19 at save)."""
    rule = await approved_rule(client, behaviour)
    await revoke(client, rule["code_sha256"])
    await _refused(
        client,
        fx.rule_set_document(behaviour, rule["code_sha256"]),
        422,
        "code_rule_not_approved",
        state="revoked",
    )


async def test_a_rule_failing_under_the_current_contract_is_refused_naming_the_case(
    client: httpx.AsyncClient, fake: FakeRunner, behaviour: str
) -> None:
    from src.core.database import sync_session_factory
    from src.services.code_rules import contract_runs

    rule = await approved_rule(client, behaviour)
    with sync_session_factory()() as db:
        contract_runs.record(
            db, rule["code_sha256"], fake.contract_version, fake.bundle_digest, failing_case="fires"
        )
        db.commit()
    await _refused(
        client,
        fx.rule_set_document(behaviour, rule["code_sha256"]),
        422,
        "code_rule_failing_under_contract",
        case="fires",
        contract_version=fake.contract_version,
    )


async def test_with_no_row_for_the_runners_minor_the_cases_run_now_and_decide(
    client: httpx.AsyncClient, fake: FakeRunner, behaviour: str
) -> None:
    """FTDD §17.4: the synchronous fallback writes the row, then decides; a twin that now
    miscounts is refused (a contract-minor bump that breaks a stored rule, FR-018.31)."""
    rule = await approved_rule(client, behaviour)
    sha = rule["code_sha256"]
    fake.contract_version = "1.1"
    function = fx.RULE_SET_SHAPES[behaviour]["function"]
    fake.twins[sha] = {function: lambda text, params: 99}
    before = len(fake.runs("save_check"))
    await _refused(
        client,
        fx.rule_set_document(behaviour, sha),
        422,
        "code_rule_failing_under_contract",
        contract_version="1.1",
    )
    assert len(fake.runs("save_check")) == before + 1
    assert count("dw_code_rule_contract_runs", "contract_version = '1.1' AND NOT passed") == 1


async def test_an_undeclared_function_and_invalid_params_are_refused(
    client: httpx.AsyncClient, fake: FakeRunner, behaviour: str
) -> None:
    rule = await approved_rule(client, behaviour)
    sha = rule["code_sha256"]
    await _refused(
        client,
        fx.rule_set_document(behaviour, sha, function="not_declared"),
        422,
        "code_rule_function_missing",
        function="not_declared",
    )
    bad = {"sums": "ten"} if behaviour == "fixture-gamma" else {"min": 0}
    details = await _refused(
        client,
        fx.rule_set_document(behaviour, sha, params=bad),
        422,
        "code_rule_params_invalid",
        signal="coded",
    )
    assert details["pointer"].startswith("/")


async def test_within_n_and_positional_uses_of_a_code_rule_are_refused(
    client: httpx.AsyncClient, fake: FakeRunner, behaviour: str
) -> None:
    rule = await approved_rule(client, behaviour)
    document = fx.rule_set_document(behaviour, rule["code_sha256"])
    within = copy.deepcopy(document)
    within["expressions"]["near"] = {
        "within_n": {
            "a": {"signal": "word"},
            "b": {"signal": "coded"},
            "n": 5,
            "unit": "code_points",
        }
    }
    await _refused(client, within, 422, "code_rule_in_within_n", signal="coded")
    anchored = copy.deepcopy(document)
    anchored["signals"].append(
        {
            "id": "near_cue",
            "kind": "proximity",
            "anchor": "coded",
            "phrases": ["cue"],
            "word_boundary": True,
            "max_distance": 3,
            "unit": "code_points",
            "side": "either",
            "ignore_case": False,
            "fold_fullwidth_ascii": False,
        }
    )
    await _refused(client, anchored, 422, "code_rule_positions_needed", use="anchor")
    vetoing = copy.deepcopy(document)
    vetoing["vetoes"] = [{"id": "v", "when": "hit", "removes": ["word"]}]
    await _refused(client, vetoing, 422, "code_rule_positions_needed", use="veto_when")


async def test_a_case_that_the_runners_counts_contradict_is_refused(
    client: httpx.AsyncClient, fake: FakeRunner, behaviour: str
) -> None:
    rule = await approved_rule(client, behaviour)
    document = fx.rule_set_document(behaviour, rule["code_sha256"])
    document["cases"][0]["expect"]["outputs"]["n"] = 7
    await _refused(client, document, 422, "case_failed", case="both")


async def test_with_the_runner_down_the_save_is_503_and_stores_nothing(
    client: httpx.AsyncClient, fake: FakeRunner, behaviour: str
) -> None:
    rule = await approved_rule(client, behaviour)
    fake.runs_down = True
    response = await client.post(
        RS, json={"document": fx.rule_set_document(behaviour, rule["code_sha256"])}
    )
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "runner_unavailable"
    assert count("dw_config_versions", "kind = 'rule_set'") == 0


async def test_a_param_change_moves_the_rule_sets_hash_only_fr_018_16(
    client: httpx.AsyncClient, fake: FakeRunner, behaviour: str
) -> None:
    rule = await approved_rule(client, behaviour)
    first = await saved_rule_set(client, fx.rule_set_document(behaviour, rule["code_sha256"]))
    params = copy.deepcopy(fx.RULE_SET_SHAPES[behaviour]["params"])
    if behaviour == "fixture-gamma":
        params["sums"].append(30)
    else:
        params["min"] = 2
    second = await saved_rule_set(
        client, fx.rule_set_document(behaviour, rule["code_sha256"], params=params)
    )
    assert second["version"] == 2 and second["sha256"] != first["sha256"]
    got = (await client.get(f"/api/v1/code-rules/by-hash/{rule['code_sha256']}")).json()
    assert got["sha256"] == rule["code_sha256"]  # the code rule's own hash never moved
    assert count("dw_code_rule_approvals") == 1  # and needed no new approval


async def test_a_preview_of_a_firing_code_rule_shows_spans_null_m21(
    client: httpx.AsyncClient, fake: FakeRunner, behaviour: str
) -> None:
    rule = await approved_rule(client, behaviour)
    document = fx.rule_set_document(behaviour, rule["code_sha256"])
    text = fx.RULE_SET_SHAPES[behaviour]["texts"][0]
    response = await client.post(f"{RS}/preview", json={"document": document, "text": text})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["signals"]["coded"]["fired"] is True
    assert body["signals"]["coded"]["spans"] is None  # never [] (FR-018.38)
    assert body["signals"]["coded"]["count"] == fx.expected_columns(behaviour, text)["n"]
    assert body["expressions"]["hit"]["spans"] is None
    assert body["outputs"] == fx.expected_columns(behaviour, text)


async def test_a_preview_with_the_runner_down_reads_the_reason_never_did_not_fire(
    client: httpx.AsyncClient, fake: FakeRunner, behaviour: str
) -> None:
    rule = await approved_rule(client, behaviour)
    document = fx.rule_set_document(behaviour, rule["code_sha256"])
    fake.runs_down = True
    response = await client.post(f"{RS}/preview", json={"document": document, "text": "x"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["signals"]["coded"]["reason"] == "rule_worker_unavailable"
    assert body["signals"]["coded"]["fired"] is None
    assert body["outputs"]["hit"] is None and body["outputs"]["quiet"] is None


async def test_an_agent_saves_a_rule_set_naming_an_approved_hash_without_any_approval(
    client: httpx.AsyncClient, fake: FakeRunner, behaviour: str
) -> None:
    """FR-018.28: running an approved hash needs no second approval anywhere."""
    rule = await approved_rule(client, behaviour)
    response = await client.post(
        RS,
        json={"document": fx.rule_set_document(behaviour, rule["code_sha256"])},
        headers=AGENT,
    )
    assert response.status_code == 201, response.text
    assert count("dw_approvals") == 0
