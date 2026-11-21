"""A generation run refreshes the runtime settings at each chunk (feature 012, FTID I6).

The run lives inside one task for hours, so ``task_prerun`` alone would leave "next check in a
running run" untrue. The boundary refresh is asserted by its call count against the chunks the run
committed. Control: remove ``runtime_settings.refresh_if_stale()`` from ``_boundary`` -> 0 calls.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from src.core import runtime_settings
from tests.support.generation_fixtures import (
    Gen,
    make_version,
    respond_template,
    run_body,
    texts,
    version_table,
)

RUNS = "/api/v1/generation-runs"


async def test_each_chunk_boundary_refreshes_the_runtime_settings(
    client: httpx.AsyncClient, gen: Gen, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings(), "generation_chunk_size", 3)
    calls: list[Any] = []
    monkeypatch.setattr(
        runtime_settings, "refresh_if_stale", lambda *a, **k: calls.append(a) or False
    )
    version = make_version(version_table(texts(8)))
    template = await respond_template(client)
    out = await client.post(RUNS, json=run_body(version, template, n_responses=2))
    assert out.status_code == 202, out.text
    run = gen.run_until_done(out.json()["id"])
    assert run.state == "completed", run.error
    assert len(calls) >= 2, "one refresh per committed chunk (8 prompts in chunks of 3)"
