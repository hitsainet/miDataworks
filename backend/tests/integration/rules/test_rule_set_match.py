"""D53 (operator 2026-10-24; G19-4 2026-11-08): a rule set states its positive explicitly.

The SSN pack is the case that forced it: inferred from every ``fired`` output, its positive is
``valid_ssn`` OR ``cued``, so a valid number with no SSN context fires alone, against D124. With
``match: {version: 1, any_of: ["cued"]}`` the positive is ``cued``. A match naming a veto's own
condition is refused on save; a document without ``match`` keeps the inference and says so.
Read through REST and the PRODUCTION MCP registry.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.rules import rule_set
from tests.integration.rules.conftest import RS, assert_refusal, save, saved
from tests.support.mcp_live import call, production_mcp, refusal_of

pytestmark = pytest.mark.usefixtures("operator_name")

PACK = Path(__file__).resolve().parents[4] / "behaviours" / "dlp-ssn-us" / "rule_set.json"


def pack_document(**over: Any) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(PACK.read_text(encoding="utf-8"))
    data = copy.deepcopy(data)
    data.update(over)
    return data


CUED = {"version": 1, "any_of": ["cued"]}


async def test_without_match_the_inference_stays_and_says_so(client: httpx.AsyncClient) -> None:
    body = await saved(client, pack_document())
    assert body["match"]["source"] == "inferred"
    assert body["match"]["any_of"] == ["valid_ssn", "cued"], "the D124 defect, stated"
    assert "No explicit match (D53)" in body["match"]["note"]
    got = (await client.get(f"{RS}/{body['name']}")).json()
    assert got["match"] == body["match"]
    assert "match" not in got["document"], "an old document is never rewritten"


async def test_an_explicit_match_is_carried_byte_for_byte(client: httpx.AsyncClient) -> None:
    document = pack_document(match=CUED)
    body = await saved(client, document)
    assert body["match"] == {"source": "explicit", "version": 1, "any_of": ["cued"], "flags": []}
    assert body["sha256"] == rule_set.digest(document)
    assert body["sha256"] != rule_set.digest(pack_document()), "the field is part of the identity"
    got = (await client.get(f"{RS}/{body['name']}", params={"version": body["version"]})).json()
    assert got["document"]["match"] == CUED, "the exported document carries the match"
    assert got["match"]["source"] == "explicit"


async def test_a_match_naming_a_veto_condition_is_refused(client: httpx.AsyncClient) -> None:
    response = await save(client, pack_document(match={"version": 1, "any_of": ["is_masked"]}))
    await assert_refusal(response, 422, "match_includes_veto_condition")
    assert response.json()["error"]["details"]["expressions"] == ["is_masked"]
    checked = await client.post(
        f"{RS}/validate",
        json={"document": pack_document(match={"version": 1, "any_of": ["is_masked"]})},
    )
    assert checked.status_code == 200, checked.text
    codes = [p["code"] for p in checked.json()["problems"]]
    assert "match_includes_veto_condition" in codes


@pytest.mark.parametrize(
    "match, words",
    [
        ({"version": 1, "any_of": ["nowhere"]}, "unknown expression"),
        ({"version": 1, "any_of": ["cued", "cued"]}, "match expression"),
        ({"version": 2, "any_of": ["cued"]}, "/match"),
        ({"version": 1, "any_of": []}, "/match"),
        (None, "never null"),
    ],
)
async def test_a_malformed_match_is_refused(
    client: httpx.AsyncClient, match: Any, words: str
) -> None:
    response = await save(client, pack_document(match=match))
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "rule_set_invalid"
    assert words in json.dumps(error), error


async def test_the_mcp_tools_carry_the_match(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, mcp_client = production_mcp(monkeypatch)
    try:
        checked = await call(
            mcp, "dataworks_validate_rule_set", {"document": pack_document(match=CUED)}
        )
        assert checked["valid"] is True and checked["match"]["any_of"] == ["cued"]
        stored = await call(mcp, "dataworks_save_rule_set", {"document": pack_document(match=CUED)})
        assert stored["match"]["source"] == "explicit"
        with pytest.raises(ToolError) as refused:
            await call(
                mcp,
                "dataworks_save_rule_set",
                {"document": pack_document(match={"version": 1, "any_of": ["is_masked"]})},
            )
    finally:
        await mcp_client.close()
    code, status, _details = refusal_of(refused.value)
    assert (code, status) == ("match_includes_veto_condition", 422)


async def test_a_stored_version_todays_model_rejects_still_reads(client: httpx.AsyncClient) -> None:
    """Review minor 2: the read miWorkbench's rule-set store uses never fails on ``match``. A stored
    version a stricter model would refuse answers its document as stored, ``match: unparsed``."""
    from sqlalchemy import text

    from src.core.database import get_sync_engine

    body = await saved(client, pack_document())
    stale = pack_document(match={"version": 1, "any_of": ["nowhere"]})
    canonical = rule_set.canonical_bytes(stale).decode("utf-8")
    with get_sync_engine().begin() as conn:
        # The row is insert-only by design; the test plays a stricter future model this way.
        conn.execute(
            text("ALTER TABLE dw_config_versions DISABLE TRIGGER dw_config_versions_insert_only")
        )
        conn.execute(
            text(
                "UPDATE dw_config_versions SET body_canonical = :c, body = CAST(:c AS jsonb), "
                "body_sha256 = :s WHERE name = :n AND number = :v"
            ),
            {"c": canonical, "s": rule_set.digest(stale), "n": body["name"], "v": body["version"]},
        )
        conn.execute(
            text("ALTER TABLE dw_config_versions ENABLE TRIGGER dw_config_versions_insert_only")
        )
    got = await client.get(f"{RS}/{body['name']}", params={"version": body["version"]})
    assert got.status_code == 200, got.text
    assert got.json()["document"]["match"] == {"version": 1, "any_of": ["nowhere"]}
    assert got.json()["match"]["source"] == "unparsed"
    assert "unknown expression" in got.json()["match"]["note"]


async def test_every_match_name_is_checked_against_the_vetoes(client: httpx.AsyncClient) -> None:
    """A veto condition second in ``any_of`` is refused as the first would be."""
    response = await save(
        client, pack_document(match={"version": 1, "any_of": ["cued", "is_masked"]})
    )
    await assert_refusal(response, 422, "match_includes_veto_condition")
    assert response.json()["error"]["details"]["expressions"] == ["is_masked"]
