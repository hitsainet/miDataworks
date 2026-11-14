"""``GET /api/v1/validators`` and ``/validators/{id}/versions/{version}`` (FR-016.5, FR-016.39;
FTASKS 6.1): derived from the LIVE registry and the packaged contract file."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from src.main import fastapi_app
from src.rules import cases as rule_cases
from src.rules.validators import VALIDATORS


async def _get(path: str, **params: Any) -> httpx.Response:
    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        return await http.get(path, params=params)


async def _all() -> list[dict[str, Any]]:
    r = await _get("/api/v1/validators", limit=200)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == len(body["items"])
    return list(body["items"])


async def test_the_list_equals_the_live_registry_validator_by_validator() -> None:
    items = await _all()
    assert [(i["id"], i["version"]) for i in items] == sorted(VALIDATORS)
    for item in items:
        decl = VALIDATORS[(item["id"], item["version"])].decl
        assert item["reasons"] == list(decl.reasons)
        assert item["params_schema"] == decl.params_schema
        assert item["superseded_by"] == (
            2 if (item["id"], item["version"]) == ("iban", 1) else None
        )
        assert item["case_count"] == len(rule_cases.load_cases()["validators"][decl.ref]["cases"])


async def test_removing_a_validator_changes_the_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(VALIDATORS, ("verhoeff", 1))
    assert ("verhoeff", 1) not in {(i["id"], i["version"]) for i in await _all()}


async def test_pages_are_disjoint_and_cover_the_registry() -> None:
    seen: list[tuple[str, int]] = []
    for page in (1, 2, 3):
        r = (await _get("/api/v1/validators", page=page, limit=5)).json()
        assert r["page"] == page and r["limit"] == 5 and r["total"] == len(VALIDATORS)
        seen += [(i["id"], i["version"]) for i in r["items"]]
    assert seen == sorted(VALIDATORS) and len(set(seen)) == len(seen)


async def test_get_returns_the_declaration_and_its_cases() -> None:
    r = await _get("/api/v1/validators/iban/versions/1")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "iban" and body["valid_path"] == "publicly_vectored"
    assert body["cases"] == rule_cases.load_cases()["validators"]["iban@1"]["cases"]


async def test_an_unregistered_version_is_404_validator_not_found() -> None:
    r = await _get("/api/v1/validators/luhn/versions/9")
    assert r.status_code == 404
    error = r.json()["error"]
    assert error["code"] == "validator_not_found" and error["details"] == {
        "validator": "luhn",
        "version": 9,
    }
