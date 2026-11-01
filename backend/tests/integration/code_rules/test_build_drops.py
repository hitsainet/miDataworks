"""Every dropping use drops a row whose code rule failed (FR-018.21; 018 Review R3).

The builder's test drove ``use: label``; FR-018.21 names label, veto AND screen. With a veto or a
screen, a failed code-rule signal's row is dropped too - never kept with a null that a later step
reads as "not vetoed" or "passed the screen" - and the drop names the signal and the reason.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.core.database import get_sync_engine
from tests.integration.code_rules.test_build_dispatch import (
    finished,
    ready,
    recipe,
    rows_of,
    rs_step,
    start,
    texts_for,
)
from tests.support.fake_runner import FakeRunner

pytestmark = pytest.mark.usefixtures("operator_name", "production_driver")


@pytest.mark.parametrize("use", ["veto", "screen"])
async def test_a_veto_or_screen_drops_the_row_whose_code_rule_failed(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    data_dir: Path,
    behaviour: str,
    use: str,
) -> None:
    from src.core import runtime_settings

    runtime_settings.apply({"code_rule_failure_share": "0.5"})
    _, rs = await ready(client, behaviour)
    texts = texts_for(behaviour)
    bad = texts[0]
    fake.fail_texts[bad] = "rule_resource_limit"
    job, _ = await start(
        client, data_dir, recipe(rs_step(rs, use=use)), texts, f"{behaviour}-{use}-drop"
    )
    production_driver.run(job)
    assert sorted(r["text"] for r in rows_of(job)) == sorted(texts[1:])
    with get_sync_engine().connect() as conn:
        events = conn.execute(
            text("SELECT reason_code, reason FROM dw_row_events WHERE kind = 'dropped'")
        ).all()
    assert [tuple(e) for e in events] == [("code_rule_failed", "coded: rule_resource_limit")]


async def test_a_version_built_now_without_code_rules_reads_recorded_and_none(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    data_dir: Path,
) -> None:
    """``0044`` sets ``code_rules_recorded`` true for every version built after it (the server
    default the model relies on): a new version that ran no code rule reads "none", never "not
    recorded" (FR-018.40)."""
    from tests.integration.code_rules.conftest import saved_rule_set
    from tests.integration.rules.conftest import fixture_document

    plain = await saved_rule_set(client, fixture_document("fixture-beta"))  # no code rule
    job, _ = await start(client, data_dir, recipe(rs_step(plain)), ["plain"], "no-code-rules")
    production_driver.run(job)
    status, result = finished(job)
    assert status == "completed" and result is not None, result
    version_id = result["version_id"]
    read = await client.get(f"/api/v1/versions/{version_id}")
    assert read.status_code == 200, read.text
    assert read.json()["code_rules"] == {"recorded": True, "code_rules": []}
