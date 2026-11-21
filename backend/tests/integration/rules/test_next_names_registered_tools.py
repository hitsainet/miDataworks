"""Every ``next`` the rule-set routes can answer names a tool of the LIVE MCP registry (FR-016.36).

The backend never imports the MCP package, so the names cannot be checked at run time: this test
drives each success and each refusal of the rule-set routes (and the resolver refusals through
recipe validation), collects every ``next.tool``, and asserts each is in the registry the
production build path serves. A renamed tool turns it red.
"""

from __future__ import annotations

import copy
from typing import Any

import httpx
import pytest

from src.rules.validators import VALIDATORS
from tests.integration.rules.conftest import RS, fixture_document, live_tools, save, saved

pytestmark = pytest.mark.usefixtures("operator_name", "production_driver")


def _next(body: Any) -> dict[str, Any] | None:
    if isinstance(body, dict) and "error" in body:
        found: dict[str, Any] | None = body["error"]["details"].get("next")
        return found
    return body.get("next") if isinstance(body, dict) else None


async def test_every_next_the_rule_routes_answer_names_a_live_tool(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, str] = {}

    def note(label: str, response: httpx.Response) -> None:
        nxt = _next(response.json())
        assert nxt is not None, (label, response.text)
        seen[label] = nxt["tool"]

    alpha = fixture_document("fixture-alpha")
    beta = fixture_document("fixture-beta")
    note("save", await save(client, alpha))
    note("get", await client.get(f"{RS}/{alpha['name']}"))
    note("validate ok", await client.post(f"{RS}/validate", json={"document": beta}))
    broken = copy.deepcopy(beta)
    broken["cases"][0]["expect"]["vetoed"] = True
    note("validate failing", await client.post(f"{RS}/validate", json={"document": broken}))
    note("preview", await client.post(f"{RS}/preview", json={"document": beta, "text": "x"}))
    note("archive", await client.post(f"{RS}/{alpha['name']}/archive"))
    note("unarchive", await client.post(f"{RS}/{alpha['name']}/unarchive"))
    note(
        "clone",
        await client.post(
            f"{RS}/{alpha['name']}/clone",
            json={"new_name": "alpha-copy", "new_behaviour_key": "fixture-beta"},
        ),
    )
    # refusals
    note("case_failed", await save(client, broken))
    no_cases = copy.deepcopy(beta)
    no_cases["cases"] = no_cases["cases"][:1]
    note("cases_missing", await save(client, no_cases))
    bad = copy.deepcopy(beta)
    bad["signals"][0]["pattern"] = r"(a)\1"
    note("pattern_refused", await save(client, bad))
    nfc = copy.deepcopy(beta)
    nfc["description"] = "é"
    note("not_nfc", await save(client, nfc))
    invalid = copy.deepcopy(beta)
    invalid["behaviour_key"] = "Pii"
    note("rule_set_invalid", await save(client, invalid))
    taken = copy.deepcopy(beta)
    taken["name"] = alpha["name"]
    note("rule_set_name_taken", await save(client, taken))
    unknown = copy.deepcopy(alpha)
    next(s for s in unknown["signals"] if s["kind"] == "validator")["validator"]["version"] = 9
    note("validator_unknown", await save(client, unknown))
    note("rule_set_not_found", await client.get(f"{RS}/nothing-here"))
    breaking = await client.post(
        f"{RS}/{beta['name']}/clone",
        json={
            "new_name": "beta-x",
            "new_behaviour_key": "fixture-alpha",
            "substitutions": [{"pointer": "/signals/0/pattern", "value": "glorpwhistle"}],
        },
    )
    assert breaking.status_code == 404 or breaking.status_code == 422, breaking.text
    if breaking.status_code == 422:
        note("clone_cases_failed", breaking)
    await saved(client, beta)
    note(
        "clone_cases_failed",
        await client.post(
            f"{RS}/{beta['name']}/clone",
            json={
                "new_name": "beta-x",
                "new_behaviour_key": "fixture-alpha",
                "substitutions": [{"pointer": "/signals/0/pattern", "value": "glorpwhistle"}],
            },
        ),
    )
    validator = VALIDATORS[("card_pan", 1)]

    def boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(validator, "check", boom)
    raising = copy.deepcopy(alpha)
    raising["name"] = "alpha-raising"
    note("rule_signal_error", await save(client, raising))
    live = await live_tools()
    assert len(seen) >= 18, seen
    missing = {label: tool for label, tool in seen.items() if tool not in live}
    assert not missing, missing
