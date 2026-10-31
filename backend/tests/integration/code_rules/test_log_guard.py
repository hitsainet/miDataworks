"""No operator text or source in any log (FR-018.25; FTASKS 6.9; M26).

A unique sentinel goes into a dry run's text, a document's source and a case's text; the dry run,
the operator's save, an agent's (gated) save and its approval, and a runner fault run with EVERY
logger at DEBUG. Neither sentinel, nor any 12-character substring of either, may appear in any
record. The runner process's own stderr is held to the same rule in
``runner/tests/test_log_guard.py``. Slice 4 adds the BUILD path: a rule set naming the rule saved
and previewed, a build of rows carrying the sentinel (one row failing with a reason, so the drop
and the reason column are written), then the runner down for the next build.
"""

from __future__ import annotations

import json
import logging
import secrets

import httpx
import pytest

from src.runner_contract.hashing import code_sha256
from tests.support import code_rule_fixtures as fx
from tests.support.fake_runner import FakeRunner

from .conftest import AGENT, CR, approve, save

pytestmark = pytest.mark.usefixtures("operator_name")


def _windows(sentinel: str) -> set[str]:
    return {sentinel[i : i + 12] for i in range(len(sentinel) - 11)}


async def test_no_text_or_source_reaches_a_log_record(
    client: httpx.AsyncClient, fake: FakeRunner, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    text_sentinel = "txt" + secrets.token_hex(16)
    source_sentinel = "src" + secrets.token_hex(16)
    body = fx.save_body("fixture-gamma")
    body["document"]["source"] += f"# {source_sentinel}\n"
    body["cases"][0]["text"] += f" {text_sentinel}"
    fake.twins[code_sha256(body["document"])] = fx.fixture("fixture-gamma")["twin"]

    dry = await client.post(
        CR + "/test",
        json={
            "document": body["document"],
            "cases": body["cases"],
            "texts": [f"a {text_sentinel} text"],
            "params": {"sums": [1]},
        },
        headers=AGENT,
    )
    assert dry.status_code == 200, dry.text
    pending = await save(client, body, agent=True)
    assert pending.status_code == 202, pending.text
    assert (await approve(client, pending.json()["approval_id"]))["status"] == "executed"
    assert (await save(client, body)).status_code == 201
    fake.down = True  # a runner fault on both paths
    down = await client.post(
        CR + "/test", json={"document": body["document"], "texts": ["x"], "params": {"sums": [1]}}
    )
    assert down.status_code == 503
    assert (await save(client, {**body, "name": "another-name"})).status_code == 503

    records = list(caplog.records)
    assert records, "the capture saw nothing: the guard would prove nothing"
    text = "\n".join(
        f"{r.name} {r.getMessage()} {json.dumps(getattr(r, '__dict__', {}), default=str)}"
        for r in records
    )
    leaked = sorted(w for s in (text_sentinel, source_sentinel) for w in _windows(s) if w in text)
    assert leaked == [], f"{len(leaked)} sentinel fragments reached a log record"


async def test_no_text_or_source_reaches_a_log_record_on_the_build_path(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: object,
    data_dir: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """FR-018.25 on the build path (FTASKS 6.9's build half; S4)."""
    from tests.integration.code_rules.conftest import saved_rule_set
    from tests.integration.code_rules.test_build_dispatch import recipe, rs_step, start

    caplog.set_level(logging.DEBUG)
    text_sentinel = "txt" + secrets.token_hex(16)
    source_sentinel = "src" + secrets.token_hex(16)
    body = fx.save_body("fixture-gamma")
    body["document"]["source"] += f"# {source_sentinel}\n"
    sha = code_sha256(body["document"])
    fake.twins[sha] = fx.fixture("fixture-gamma")["twin"]
    assert (await save(client, body)).status_code == 201
    rs = await saved_rule_set(client, fx.rule_set_document("fixture-gamma", sha))
    preview = await client.post(
        "/api/v1/rule-sets/preview",
        json={"document": fx.rule_set_document("fixture-gamma", sha), "text": text_sentinel},
    )
    assert preview.status_code == 200, preview.text
    from src.core import runtime_settings

    runtime_settings.apply({"code_rule_failure_share": "0.9"})
    rows = [f"tally 28 {text_sentinel}", f"{text_sentinel} 11", "plain"]
    fake.fail_texts[rows[1]] = "rule_resource_limit"
    job, source = await start(
        client, data_dir, recipe(rs_step(rs, use="label")), rows, "log-guard"  # type: ignore[arg-type]
    )
    production_driver.run(job)  # type: ignore[attr-defined]
    fake.down = True
    again, _ = await start(
        client, data_dir, recipe(rs_step(rs, use="decoy_check")), [], "log-guard2", source=source  # type: ignore[arg-type]
    )
    production_driver.run(again)  # type: ignore[attr-defined]
    from tests.integration.code_rules.test_build_dispatch import finished

    assert finished(job)[0] == "completed" and finished(again)[0] == "completed"
    records = list(caplog.records)
    assert any("rule_set" in r.getMessage() or "step" in r.getMessage() for r in records)
    text = "\n".join(
        f"{r.name} {r.getMessage()} {json.dumps(getattr(r, '__dict__', {}), default=str)}"
        for r in records
    )
    leaked = sorted(w for s in (text_sentinel, source_sentinel) for w in _windows(s) if w in text)
    assert leaked == [], f"{len(leaked)} sentinel fragments reached a log record"
