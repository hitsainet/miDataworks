"""DR-14 and DR-15: the stored ``mistudio_*`` columns and ``dw_mistudio_registrations`` stay
(feature 020; FR-020.2; R1: no stored value or name is rewritten).

Rows are written by the PRODUCTION writers (the ORM classes the services use, the send worker's own
insert) and read back through the API answer AND a raw ``SELECT`` naming the column or table, so a
renamed attribute, answer field, column or table turns this red (controls M2b, M3)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import jsonschema
from sqlalchemy import text

from src.core.database import get_sync_engine, sync_session_factory
from src.core.ids import new_id
from src.models.detector_results import RewardMark
from src.models.reproduction_link import ReproductionLink
from src.services.sources import spot_check
from tests.integration.detector_sets.test_reproduction_links import labelled_version
from tests.integration.detector_sets.test_results_and_marks import sent_set
from tests.integration.frozen.test_kinds import spot_row
from tests.support.frozen import exercises
from tests.support.publish_fixtures import publisher  # noqa: F401 - fixture
from tests.support.send_fixtures import API, SendDriver, sender  # noqa: F401 - fixture
from tests.support.version_fixtures import driver  # noqa: F401 - fixture

BASE = "http://bench-frozen.test:8000"


def _raw(sql: str, **params: Any) -> list[Any]:
    with get_sync_engine().connect() as conn:
        return list(conn.execute(text(sql), params).all())


def _mark() -> str:
    with sync_session_factory()() as db:
        row = RewardMark(
            id=new_id("rwm"),
            mistudio_base_url=BASE,
            mistudio_probe_id="pm_frozen",
            source="operator",
            reason="trained on it",
            marked_by="Test Operator",
            marked_by_origin="operator",
        )
        db.add(row)
        db.commit()
        return row.id


def _link(version_id: str) -> str:
    with sync_session_factory()() as db:
        row = ReproductionLink(
            id=new_id("rpl"),
            mistudio_base_url=BASE,
            mistudio_probe_id="pm_frozen",
            mistudio_run_id="pmr_frozen",
            probe_dataset_id="pmd_frozen",
            role="id_test",
            version_id=version_id,
            split="test",
            input_column="text",
            label_column="label",
            label_mapping={"a": "positive", "b": "negative"},
            auroc=0.9,
            ci_low=0.89,
            ci_high=0.91,
            n_positive=1,
            n_negative=1,
            check_level="counts_only",
            checks={},
            scoring_form={},
            evidence={},
            created_by="Test Operator",
            created_by_origin="operator",
        )
        db.add(row)
        db.commit()
        return row.id


@exercises(
    "DR-14",
    literals=("mistudio_probe_id", "millm_probe_id", "mistudio_base_url", "mistudio_run_id"),
)
async def test_dr14_columns_round_trip_through_orm_and_api(
    client: httpx.AsyncClient, data_dir: Path, literal: str
) -> None:
    if literal == "millm_probe_id":
        # miStudio's spot-check export carries it; the vendored schema the upload is recognised
        # from names it, and a row holding it is valid.
        document = json.loads(spot_check.VENDORED_PATH.read_bytes())
        assert literal in document["properties"]
        assert not list(jsonschema.Draft202012Validator(document).iter_errors(spot_row()))
        return
    if literal == "mistudio_run_id":
        version_id = labelled_version(data_dir, [("a", "a"), ("b", "b")])
        link_id = _link(version_id)
        answer = (await client.get(f"{API}/reproduction-links/{link_id}")).json()
        assert answer[literal] == "pmr_frozen"
        assert _raw(f"SELECT {literal} FROM dw_reproduction_links") == [("pmr_frozen",)]
        return
    _mark()
    [answer] = (await client.get(f"{API}/reward-marks")).json()["items"]
    expected = {"mistudio_base_url": BASE, "mistudio_probe_id": "pm_frozen"}[literal]
    assert answer[literal] == expected
    assert _raw(f"SELECT {literal} FROM dw_reward_marks") == [(expected,)]


@exercises("DR-15", literals=("dw_mistudio_registrations",))
async def test_dr15_table_written_by_the_send_worker(
    client: httpx.AsyncClient,
    operator_name: str,
    sender: SendDriver,  # noqa: F811
    literal: str,
) -> None:
    _, send = await sent_set(client, sender)
    rows = _raw(f"SELECT kind, mistudio_base_url, mistudio_dataset_id FROM {literal}")
    assert rows, "the send worker wrote no registration"
    assert {kind for kind, _, _ in rows} >= {"dataset"}
    assert all(base == send["mistudio_base_url"] and dataset for _, base, dataset in rows)
