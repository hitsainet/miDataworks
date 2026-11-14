"""DR-48: the stored JSON keys ``mistudio`` / ``millm`` inside a reproduction link stay (feature 020;
FR-020.2; FTID ID-17). A link's ``checks`` and ``scoring_form`` are written once by the link route
and read back by every gate; a renamed key would make every stored link unreadable (control M65).

The link is made through the real route, against the fake miStudio serving the recorded run, then
read back through ``GET /api/v1/reproduction-links/{id}`` and from the stored row itself."""

from __future__ import annotations

from pathlib import Path

import httpx
from sqlalchemy import text

from src.core.database import get_sync_engine
from tests.integration.detector_sets.test_reproduction_links import (
    gate_texts,
    labelled_version,
    link_body,
    studio,  # noqa: F401 - fixture
)
from tests.support.fake_miworkbench import FakeMiWorkbench
from tests.support.frozen import exercises
from tests.support.send_fixtures import API


@exercises("DR-48", literals=("mistudio", "millm"))
async def test_reproduction_and_paired_source_keys_round_trip(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    studio: FakeMiWorkbench,  # noqa: F811
    literal: str,
) -> None:
    version_id = labelled_version(data_dir, gate_texts())
    made = await client.post(f"{API}/reproduction-links", json=link_body(version_id))
    assert made.status_code == 201, made.text
    link_id = made.json()["id"]
    answer = (await client.get(f"{API}/reproduction-links/{link_id}")).json()
    assert literal in answer["scoring_form"], f"scoring_form.{literal} is gone"
    with get_sync_engine().connect() as conn:
        checks, form = conn.execute(
            text("SELECT checks, scoring_form FROM dw_reproduction_links WHERE id = :i"),
            {"i": link_id},
        ).one()
    assert literal in form, f"the stored scoring_form has no {literal!r} key"
    if literal == "mistudio":
        assert answer["checks"]["row_count"]["mistudio"] == 60
        assert set(answer["checks"]["class_balance"]["mistudio"]) == {"positive", "negative"}
        assert checks["row_count"]["mistudio"] == 60
