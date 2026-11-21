"""Rule sets: save, refusals, validate, preview, list, get, archive (FR-016.16 – FR-016.20, .37, .39).

Real PostgreSQL and the live app over HTTP. Both fixture behaviours wherever a test reads a document.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.core import runtime_settings
from src.core.database import get_sync_engine
from tests.integration.rules.conftest import (
    AGENT,
    RS,
    assert_refusal,
    fixture_document,
    live_tools,
    save,
    saved,
)

pytestmark = pytest.mark.usefixtures("operator_name")


def _count(table: str, where: str = "TRUE") -> int:
    with get_sync_engine().connect() as conn:
        return int(
            conn.execute(text(f"SELECT count(*) FROM {table} WHERE {where}")).scalar()
        )  # noqa: S608


def _row_counts() -> dict[str, int]:
    return {t: _count(t) for t in ("dw_rule_sets", "dw_config_versions", "dw_rule_set_uses")}


# --- save and versions (FR-016.16) --------------------------------------------------------------


async def test_save_numbers_versions_and_the_hash_follows_the_canonical_body(
    client: httpx.AsyncClient, document: dict[str, Any]
) -> None:
    first = await saved(client, document)
    assert first["version"] == 1 and first["created"] is True
    assert first["behaviour_key"] == document["behaviour_key"]
    assert first["next"]["tool"] in await live_tools()
    # key order is not content: the same version answers, nothing new is stored
    reordered = dict(reversed(list(document.items())))
    again = await save(client, reordered)
    assert again.status_code == 201 and again.json()["version"] == 1
    assert again.json()["sha256"] == first["sha256"] and again.json()["created"] is False
    # an edit is version 2 with another hash
    edited = copy.deepcopy(document)
    edited["description"] += " Edited."
    second = await saved(client, edited)
    assert second["version"] == 2 and second["sha256"] != first["sha256"]
    got = (await client.get(f"{RS}/{document['name']}", params={"version": 1})).json()
    assert got["superseded_by"] == 2 and got["sha256"] == first["sha256"]
    assert got["document"] == document
    latest = (await client.get(f"{RS}/{document['name']}")).json()
    assert latest["version"] == 2 and latest["superseded_by"] is None


async def test_the_stored_bytes_are_the_rfc_8785_bytes_the_sha256_names(
    client: httpx.AsyncClient, document: dict[str, Any]
) -> None:
    from src.rules import rule_set

    out = await saved(client, document)
    with get_sync_engine().connect() as conn:
        row = conn.execute(
            text(
                "SELECT body_canonical, body_sha256, plugin, kind FROM dw_config_versions "
                "WHERE name = :n"
            ),
            {"n": document["name"]},
        ).one()
    assert row[0].encode("utf-8") == rule_set.canonical_bytes(document)
    assert row[1] == rule_set.digest(document) == out["sha256"]
    assert (row[2], row[3]) == ("rule_set", "rule_set")


async def test_concurrent_saves_of_one_name_never_answer_500(client: httpx.AsyncClient) -> None:
    base = fixture_document("fixture-beta")
    docs = []
    for n in range(4):
        d = copy.deepcopy(base)
        d["description"] = f"Variant {n}."
        docs.append(d)
    answers = await asyncio.gather(*(save(client, d) for d in docs))
    statuses = sorted(a.status_code for a in answers)
    assert all(s in (201, 409) for s in statuses), [a.text for a in answers]
    numbers = sorted(a.json()["version"] for a in answers if a.status_code == 201)
    assert numbers == list(range(1, len(numbers) + 1))
    for a in answers:
        if a.status_code == 409:
            assert a.json()["error"]["code"] == "rule_set_conflict"


# --- refusals (FR-016.17): code, the named item and a next naming a live tool ---------------------


async def test_refused_rule_set_invalid_without_behaviour_key(
    client: httpx.AsyncClient, document: dict[str, Any]
) -> None:
    del document["behaviour_key"]
    before = _row_counts()
    await assert_refusal(
        await save(client, document), 422, "rule_set_invalid", pointer="/behaviour_key"
    )
    assert _row_counts() == before


async def test_refused_rule_set_invalid_with_an_upper_case_key(
    client: httpx.AsyncClient, document: dict[str, Any]
) -> None:
    document["behaviour_key"] = "Pii"
    await assert_refusal(
        await save(client, document), 422, "rule_set_invalid", pointer="/behaviour_key"
    )


async def test_refused_pattern_outside_the_dialect(client: httpx.AsyncClient) -> None:
    document = fixture_document("fixture-alpha")
    index = next(i for i, s in enumerate(document["signals"]) if s["kind"] == "pattern")
    document["signals"][index]["pattern"] = r"(a)\1"
    details = await assert_refusal(
        await save(client, document), 422, "pattern_refused", rule="backreference"
    )
    assert details["pointer"] == f"/signals/{index}/pattern"
    assert details["position"] == 3


async def test_refused_validator_unknown(client: httpx.AsyncClient) -> None:
    document = fixture_document("fixture-alpha")
    signal = next(s for s in document["signals"] if s["kind"] == "validator")
    signal["validator"]["version"] = 99
    details = await assert_refusal(
        await save(client, document), 422, "validator_unknown", version=99
    )
    assert details["next"]["tool"] == "dataworks_list_validators"


async def test_refused_case_failed_naming_the_case(
    client: httpx.AsyncClient, document: dict[str, Any]
) -> None:
    # The veto verdict, so the minimum (a firing and a non-firing case per output) still holds.
    case = next(c for c in document["cases"] if not c["expect"]["vetoed"])
    case["expect"]["vetoed"] = True
    details = await assert_refusal(
        await save(client, document), 422, "case_failed", case=case["id"]
    )
    assert details["expected"]["vetoed"] is True and details["actual"]["vetoed"] is False
    assert details["signals"] and set(details["deciding"]) == {
        o["column"] for o in document["outputs"]
    }


async def test_refused_cases_missing_names_the_output(
    client: httpx.AsyncClient, document: dict[str, Any]
) -> None:
    from src.rules.cases import fires

    column = document["outputs"][0]["column"]
    document["cases"] = [c for c in document["cases"] if fires(c["expect"]["outputs"][column])]
    details = await assert_refusal(await save(client, document), 422, "cases_missing")
    assert {"output": column, "needs": "a case where it does not fire"} in details["missing"]


async def test_refused_cases_missing_for_a_veto_with_no_vetoed_case(
    client: httpx.AsyncClient,
) -> None:
    document = fixture_document("fixture-alpha")
    assert document["vetoes"], "fixture-alpha declares a veto"
    document["cases"] = [c for c in document["cases"] if not c["expect"]["vetoed"]]
    details = await assert_refusal(await save(client, document), 422, "cases_missing")
    assert any(m.get("veto") == document["vetoes"][0]["id"] for m in details["missing"])


async def test_a_code_rule_signal_is_refused_as_switched_off_while_code_rules_are_off(
    client: httpx.AsyncClient, document: dict[str, Any]
) -> None:
    """Feature 018 slice 4 (FR-018.37; FTASKS 13.3): 016's ``code_rule_not_available`` is replaced
    by 018's checks; with ``CODE_RULES_ENABLED`` off (the default, D62) the first is
    ``code_rules_disabled`` naming the signal. 018's other refusals:
    ``tests/integration/code_rules/test_rule_set_code_rules.py``."""
    document["signals"].append(
        {
            "id": "coded",
            "kind": "code_rule",
            "code_sha256": "a" * 64,
            "runner_contract": "midataworks.runner/v1",
            "function": "detect",
            "params": {},
            "outputs": [{"name": "hit", "type": "bool"}],
        }
    )
    details = await assert_refusal(
        await save(client, document), 409, "code_rules_disabled", signal="coded"
    )
    assert details["next"] == {
        "tool": "dataworks_howto",
        "args": {"topic": "code_rules"},
        "why": details["next"]["why"],
    }


async def test_refused_not_nfc_names_the_pointer(
    client: httpx.AsyncClient, document: dict[str, Any]
) -> None:
    document["description"] = "Café rules."
    await assert_refusal(await save(client, document), 422, "not_nfc", pointer="/description")


async def test_refused_a_name_owned_by_another_behaviour_key(client: httpx.AsyncClient) -> None:
    alpha = fixture_document("fixture-alpha")
    await saved(client, alpha)
    beta = fixture_document("fixture-beta")
    beta["name"] = alpha["name"]
    details = await assert_refusal(
        await save(client, beta), 409, "rule_set_name_taken", behaviour_key="fixture-alpha"
    )
    assert details["next"]["tool"] == "dataworks_clone_rule_set"
    assert _count("dw_config_versions", "kind = 'rule_set'") == 1


async def test_refused_provenance_naming_no_derivation(
    client: httpx.AsyncClient, document: dict[str, Any]
) -> None:
    document["provenance"] = {
        "kind": "derived",
        "derivation_id": "rdv_missing",
        "method": "sequential_cover",
        "method_version": 1,
        "source_version_id": "00000000-0000-0000-0000-000000000000",
        "label_column": "label",
        "seed": 1,
        "params_sha256": "b" * 64,
    }
    response = await save(client, document)
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "derivation_not_found"
    assert _count("dw_config_versions", "kind = 'rule_set'") == 0


# --- caps (FR-016.37): one over each limit refused naming the setting; the setting decides ------

CAP_CASES = {
    "rule_set_max_signals": ("RULE_SET_MAX_SIGNALS", lambda d: len(d["signals"])),
    "rule_set_max_cases": ("RULE_SET_MAX_CASES", lambda d: len(d["cases"])),
    "rule_set_max_document_bytes": (
        "RULE_SET_MAX_DOCUMENT_BYTES",
        lambda d: len(__import__("src.rules.rule_set", fromlist=["x"]).canonical_bytes(d)),
    ),
}


@pytest.mark.parametrize("key", sorted(CAP_CASES))
async def test_each_cap_refuses_one_over_and_the_setting_changes_the_result(
    client: httpx.AsyncClient, document: dict[str, Any], key: str
) -> None:
    env, size = CAP_CASES[key]
    if key == "rule_set_max_document_bytes":
        document["description"] = "Padded. " * 200  # past the setting's 1024-byte floor
    limit = size(document) - 1
    runtime_settings.apply({key: str(limit)})
    response = await save(client, document)
    assert response.status_code == 413, response.text
    error = response.json()["error"]
    assert error["code"] == "cap_exceeded"
    assert error["details"]["setting"] == env and error["details"]["limit"] == limit
    assert error["details"]["actual"] == limit + 1
    runtime_settings.apply({key: str(size(document))})
    assert (await save(client, document)).status_code == 201


async def test_the_terms_cap_reads_each_lexical_signal(client: httpx.AsyncClient) -> None:
    document = fixture_document("fixture-beta")
    document["signals"].append(
        {
            "id": "lex",
            "kind": "lexical_weights",
            "terms": [{"term": "alpha", "weight": 1.0}, {"term": "beta", "weight": 1.0}],
            "cut": 1.0,
            "counting": "presence",
            "ignore_case": True,
            "fold_fullwidth_ascii": False,
            "word_boundary": True,
        }
    )
    runtime_settings.apply({"rule_set_max_terms": "1"})
    response = await save(client, document)
    assert response.status_code == 413
    assert response.json()["error"]["details"] == {
        "setting": "RULE_SET_MAX_TERMS",
        "limit": 1,
        "actual": 2,
        "signal": "lex",
    }
    runtime_settings.apply({"rule_set_max_terms": "2"})
    assert (await save(client, document)).status_code == 201


async def test_the_code_params_cap_is_checked_before_the_code_rule_refusal(
    client: httpx.AsyncClient,
) -> None:
    document = fixture_document("fixture-beta")
    document["signals"].append(
        {
            "id": "coded",
            "kind": "code_rule",
            "code_sha256": "a" * 64,
            "runner_contract": "midataworks.runner/v1",
            "function": "detect",
            "params": {"words": ["x" * 40]},
            "outputs": [{"name": "hit", "type": "bool"}],
        }
    )
    runtime_settings.apply({"rule_set_max_code_params_bytes": "10"})
    response = await save(client, document)
    assert response.status_code == 413
    assert response.json()["error"]["details"]["setting"] == "RULE_SET_MAX_CODE_PARAMS_BYTES"
    runtime_settings.apply({"rule_set_max_code_params_bytes": "16384"})
    assert (await save(client, document)).json()["error"]["code"] == "code_rules_disabled"


# --- validate (FR-016.18) ------------------------------------------------------------------------


async def test_validate_reports_every_failing_case_and_writes_nothing(
    client: httpx.AsyncClient, document: dict[str, Any]
) -> None:
    broken = copy.deepcopy(document)
    for case in broken["cases"][:2]:
        case["expect"]["vetoed"] = not case["expect"]["vetoed"]
    before = _row_counts()
    answer = await client.post(f"{RS}/validate", json={"document": broken})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["valid"] is False and body["cases_run"] == len(broken["cases"])
    failing = {f["case"]: f for f in body["failing_cases"]}
    assert set(failing) == {c["id"] for c in broken["cases"][:2]}
    for report in failing.values():
        assert report["signals"] and report["deciding"] and report["mismatches"]
        assert report["expected"] != report["actual"]
    assert body["next"]["tool"] == "dataworks_validate_rule_set"
    assert _row_counts() == before
    ok = (await client.post(f"{RS}/validate", json={"document": document})).json()
    assert ok["valid"] is True and ok["failing_cases"] == [] and ok["problems"] == []
    assert ok["next"]["tool"] == "dataworks_save_rule_set"


async def test_validate_a_stored_version_by_name(
    client: httpx.AsyncClient, document: dict[str, Any]
) -> None:
    out = await saved(client, document)
    body = (
        await client.post(f"{RS}/validate", json={"name": document["name"], "version": 1})
    ).json()
    assert body["valid"] is True and body["sha256"] == out["sha256"]


async def test_validate_collects_problems_from_several_stages(client: httpx.AsyncClient) -> None:
    document = fixture_document("fixture-alpha")
    index = next(i for i, s in enumerate(document["signals"]) if s["kind"] == "pattern")
    document["signals"][index]["pattern"] = r"(?=a)"
    validator = next(s for s in document["signals"] if s["kind"] == "validator")
    validator["validator"]["version"] = 99
    body = (await client.post(f"{RS}/validate", json={"document": document})).json()
    assert [p["code"] for p in body["problems"]] == ["pattern_refused", "validator_unknown"]


# --- preview (FR-016.19) -------------------------------------------------------------------------


async def test_preview_reports_signals_outputs_and_writes_nothing(
    client: httpx.AsyncClient, document: dict[str, Any]
) -> None:
    case = document["cases"][0]
    from src.rules.cases import case_input

    text_value, element = case_input(case)
    before = _row_counts()
    answer = await client.post(
        f"{RS}/preview", json={"document": document, "text": text_value, "element": element}
    )
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["outputs"] == case["expect"]["outputs"]
    assert body["vetoed"] is case["expect"]["vetoed"]
    assert body["spans_unit"] == "code_points"
    assert set(body["signals"]) == {s["id"] for s in document["signals"]}
    for verdict in body["signals"].values():
        assert set(verdict) == {"fired", "spans", "substrings", "outputs"}
    assert set(body["deciding"]) == {o["column"] for o in document["outputs"]}
    assert _row_counts() == before


async def test_preview_of_a_stored_rule_set_by_name(
    client: httpx.AsyncClient, document: dict[str, Any]
) -> None:
    await saved(client, document)
    body = (
        await client.post(f"{RS}/preview", json={"name": document["name"], "text": "nothing"})
    ).json()
    assert set(body["outputs"]) == {o["column"] for o in document["outputs"]}


async def test_preview_over_the_size_cap_names_the_setting(
    client: httpx.AsyncClient, document: dict[str, Any]
) -> None:
    runtime_settings.apply({"rule_preview_max_text_bytes": "8"})
    answer = await client.post(f"{RS}/preview", json={"document": document, "text": "é" * 5})
    assert answer.status_code == 413
    assert answer.json()["error"]["code"] == "preview_text_too_large"
    assert answer.json()["error"]["details"] == {
        "setting": "RULE_PREVIEW_MAX_TEXT_BYTES",
        "limit": 8,
        "actual": 10,
    }
    runtime_settings.apply({"rule_preview_max_text_bytes": "10"})
    ok = await client.post(f"{RS}/preview", json={"document": document, "text": "é" * 5})
    assert ok.status_code == 200


SENTINEL = "Vexilloquant 4111 1111 1111 1111 zantimere quorbish sentinel-7c1e"


async def test_preview_text_reaches_no_log_record_at_any_level(
    client: httpx.AsyncClient,
    document: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-016.19: success, the over-size refusal and a forced evaluation error; the sentinel and a
    12-character piece of it appear in no record (message, arguments or exception text)."""
    piece = SENTINEL[13:25]
    caplog.set_level(logging.DEBUG)
    ok = await client.post(f"{RS}/preview", json={"document": document, "text": SENTINEL})
    assert ok.status_code == 200
    runtime_settings.apply({"rule_preview_max_text_bytes": "8"})
    big = await client.post(f"{RS}/preview", json={"document": document, "text": SENTINEL})
    assert big.status_code == 413
    runtime_settings.reset()
    from src.rules import compose

    def explode(self: Any, text_value: str, element: str | None = None) -> Any:
        raise ValueError(f"evaluation failed on {text_value!r}")

    monkeypatch.setattr(compose.CompiledRuleSet, "evaluate", explode)
    failed = await client.post(f"{RS}/preview", json={"document": document, "text": SENTINEL})
    assert failed.status_code == 422 and failed.json()["error"]["code"] == "rule_signal_error"
    assert SENTINEL not in failed.text and piece not in failed.text
    assert caplog.records, "the preview logs its counts"
    for record in caplog.records:
        rendered = record.getMessage() + repr(record.args) + str(record.exc_info or "")
        assert SENTINEL not in rendered and piece not in rendered, record


async def test_a_raising_validator_is_named_without_the_text(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.rules.validators import VALIDATORS

    document = fixture_document("fixture-alpha")
    validator = VALIDATORS[("card_pan", 1)]

    def boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(validator, "check", boom)
    answer = await client.post(f"{RS}/preview", json={"document": document, "text": SENTINEL})
    assert answer.status_code == 422, answer.text
    error = answer.json()["error"]
    assert error["code"] == "rule_signal_error"
    assert error["details"] == {"signal": "card", "validator": "card_pan@1"}
    assert "4111" not in answer.text


# --- list, get, archive (FR-016.20, FR-016.39) ---------------------------------------------------


async def test_the_behaviour_filter_separates_two_sets_that_differ_only_in_key(
    client: httpx.AsyncClient,
) -> None:
    alpha = fixture_document("fixture-alpha")
    other = copy.deepcopy(alpha)
    other["name"], other["behaviour_key"] = "fixture-alpha-twin", "fixture-beta"
    await saved(client, alpha)
    await saved(client, other)
    a = (await client.get(RS, params={"behaviour_key": "fixture-alpha"})).json()
    b = (await client.get(RS, params={"behaviour_key": "fixture-beta"})).json()
    assert [i["name"] for i in a["items"]] == [alpha["name"]]
    assert [i["name"] for i in b["items"]] == ["fixture-alpha-twin"]
    item = a["items"][0]
    assert item["counts"] == {
        "signals": len(alpha["signals"]),
        "outputs": len(alpha["outputs"]),
        "vetoes": len(alpha["vetoes"]),
        "cases": len(alpha["cases"]),
    }
    assert item["last_validation"]["passed"] is True and item["version"] == 1


async def test_pages_are_disjoint_and_archive_hides_until_asked(
    client: httpx.AsyncClient,
) -> None:
    beta = fixture_document("fixture-beta")
    names = []
    for n in range(5):
        d = copy.deepcopy(beta)
        d["name"] = f"words-{n}"
        names.append((await saved(client, d))["name"])
    p1 = (await client.get(RS, params={"page": 1, "limit": 2})).json()
    p2 = (await client.get(RS, params={"page": 2, "limit": 2})).json()
    p3 = (await client.get(RS, params={"page": 3, "limit": 2})).json()
    seen = [i["name"] for p in (p1, p2, p3) for i in p["items"]]
    assert seen == sorted(names) and p1["total"] == 5
    archived = await client.post(
        f"{RS}/words-0/archive", json={"reason": "superseded"}, headers=AGENT
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["archived"] is True
    assert archived.json()["next"]["tool"] in await live_tools()
    assert "words-0" not in [i["name"] for i in (await client.get(RS)).json()["items"]]
    shown = (await client.get(RS, params={"include_archived": True})).json()
    assert "words-0" in [i["name"] for i in shown["items"]]
    assert _count("dw_archive_events", "entity_kind = 'rule_set' AND action = 'archive'") == 1
    back = await client.post(f"{RS}/words-0/unarchive")
    assert back.status_code == 200 and back.json()["archived"] is False
    assert "words-0" in [i["name"] for i in (await client.get(RS)).json()["items"]]


async def test_get_and_archive_of_an_unknown_name_are_404_with_next(
    client: httpx.AsyncClient,
) -> None:
    await assert_refusal(await client.get(f"{RS}/no-such-set"), 404, "rule_set_not_found")
    await assert_refusal(await client.post(f"{RS}/no-such-set/archive"), 404, "rule_set_not_found")


# --- the generic config-version routes keep their kinds (FR-016.16; FTDD 016 section 4.3) -------


async def test_the_generic_routes_refuse_and_hide_rule_sets(
    client: httpx.AsyncClient, document: dict[str, Any]
) -> None:
    refused = await client.post(
        "/api/v1/config-versions",
        json={"kind": "rule_set", "name": "x", "body": {"plugin": "rule_set"}},
    )
    assert refused.status_code == 422
    await saved(client, document)
    listed = await client.get("/api/v1/config-versions")
    assert listed.status_code == 200, listed.text
    assert listed.json()["items"] == []
    by_kind = await client.get("/api/v1/config-versions", params={"kind": "rule_set"})
    assert by_kind.status_code == 200 and by_kind.json()["items"] == []
    with get_sync_engine().connect() as conn:
        version_id = conn.execute(
            text("SELECT id FROM dw_config_versions WHERE kind = 'rule_set'")
        ).scalar_one()
    gone = await client.get(f"/api/v1/config-versions/{version_id}")
    assert gone.status_code == 404 and gone.json()["error"]["code"] == "config_not_found"


# --- the pilot pack (FR-016.P3): data under behaviours/, saved and validated like any other ------


async def test_the_pilot_pack_validates_and_saves(client: httpx.AsyncClient) -> None:
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[4] / "behaviours"
    packs = sorted(root.glob("*/rule_set.json"))
    assert packs, "the pilot pack ships under behaviours/"
    for path in packs:
        document = json.loads(path.read_text())
        meta = json.loads((path.parent / "behaviour.json").read_text())
        assert document["behaviour_key"] == meta["behaviour_key"] == path.parent.name
        checked = (await client.post(f"{RS}/validate", json={"document": document})).json()
        assert checked["valid"] is True, checked
        out = await saved(client, document)
        assert out["behaviour_key"] == meta["behaviour_key"]
