"""``GET /operators`` shows ``conformance``; the upgrade plan shows why ``regex_match@1`` is not
conformant beside the move to ``@2`` (FTASKS 4.3, 4.5). Both read the live app and registry."""

from __future__ import annotations

from typing import Any

import httpx

from src.main import fastapi_app
from src.rules.conformance import CONFORMANT, NOT_CONFORMANT


async def _call(method: str, path: str, **kw: Any) -> httpx.Response:
    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        return await http.request(method, path, **kw)


async def test_the_operator_list_carries_conformance() -> None:
    r = await _call("GET", "/api/v1/operators", params={"provider": "native", "limit": 200})
    assert r.status_code == 200, r.text
    by_ref = {i["ref"]: i for i in r.json()["items"]}
    assert by_ref["regex_match@2"]["conformance"] == {
        "conformant": True,
        "scope": CONFORMANT["regex_match@2"].description,
    }
    assert by_ref["regex_match@1"]["conformance"] == {
        "conformant": False,
        "reason": NOT_CONFORMANT["regex_match@1"],
    }
    assert by_ref["checksum_validate@1"]["conformance"]["conformant"] is True
    assert by_ref["rule_label@1"]["conformance"] is None


async def test_the_upgrade_plan_names_the_reason_beside_the_move() -> None:
    body = {
        "recipe_body": {
            "format": "dw.recipe/v1",
            "steps": [
                {
                    "operator": "regex_match",
                    "version": "1",
                    "params": {"column": "text", "pattern": "a"},
                },
                {
                    "operator": "regex_match",
                    "version": "2",
                    "params": {"column": "text", "pattern": "a", "fold_fullwidth_ascii": False},
                },
                {"operator": "rule_label", "version": "1", "params": {"rules": ["x if a == 1"]}},
            ],
        }
    }
    r = await _call("POST", "/api/v1/operators/upgrade-plan", json=body)
    assert r.status_code == 200, r.text
    first, second, third = r.json()["steps"]
    assert (first["from"], first["to"], first["changes"]) == ("1", "2", True)
    assert first["conformance_note"] == NOT_CONFORMANT["regex_match@1"]
    assert first["params_valid"] is False  # fold_fullwidth_ascii must be stated
    assert second["changes"] is False and second["conformance_note"] is None
    assert third["conformance_note"] is None
