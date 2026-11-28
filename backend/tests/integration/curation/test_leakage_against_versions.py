"""``against_version_ids``: one version's splits against other versions' splits (FU-7a gap (n)).

CHK1 had to build fu7a v36, a concatenation of four versions, to ask whether the fresh and reserve
splits leak into v56's and v37's; a single-split version (A-RS) could not be checked at all
(``no_splits``). The route now takes the other versions; their splits are sides named
``<split>@<version id>`` (so a ``train`` on both sides stays two sides), and pairs inside them are
not reported.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pyarrow as pa

from src.core.database import sync_session_factory
from src.models import VersionReport
from src.services.curation import leakage_service
from tests.fixtures.humor_pool import with_system_columns
from tests.support.curation_fixtures import make_version

ROLES = {"text": "content", "carrier_id": "metadata"}
SHARED = "the clerk stamped every sheet in the folder with the date of arrival and left"
OLD_INTERNAL = "a sentence that the old version holds in both of its own splits for this test"


def _table(rows: list[tuple[str, str, str | None]]) -> pa.Table:
    table = with_system_columns([{"text": t, "carrier_id": c} for t, _, c in rows])
    return table.set_column(
        table.schema.get_field_index("_dw_split"), "_dw_split", pa.array([s for _, s, _ in rows])
    )


def _versions(db: object) -> tuple[str, str]:
    subject = make_version(
        db,
        _table(
            [
                (SHARED, "train", "c1"),  # a `train` here AND in the old version
                ("a reserve row nobody else has seen at any time before", "reserve", "c2"),
                ("social security number ssn", "reserve", "c3"),
            ]
        ),
        ROLES,
    )
    old_rows = _table(
        [
            (SHARED, "train", "c9"),
            ("SOCIAL SECURITY NUMBER: SSN", "test", "c3"),
            (OLD_INTERNAL, "train", "c7"),
            (OLD_INTERNAL, "test", "c8"),  # inside the old version: not this check's question
        ]
    )
    old = make_version(db, old_rows.drop_columns(["carrier_id"]), {"text": "content"})
    return str(subject.id), str(old.id)


async def test_a_version_is_checked_against_another_without_a_concatenation(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    with sync_session_factory()() as db:
        subject, old = _versions(db)
    response = await client.post(
        f"/api/v1/versions/{subject}/leakage",
        json={"against_version_ids": [old], "group_column": "carrier_id"},
    )
    assert response.status_code == 200, response.text
    report = response.json()["report"]
    result = report["result"]
    assert result["exact_pairs"] == {f"train|train@{old}": 1}
    assert result["near_pairs"] == {f"reserve|test@{old}": 1}
    assert result["group_pairs"] == {}  # the old version has no carrier_id: no groups there
    assert result["subject_sides"] == ["reserve", "train"]
    assert sorted(result["sides"]) == sorted(["reserve", "train", f"test@{old}", f"train@{old}"])
    assert report["inputs"] == [
        {"version_id": subject},
        {"version_id": old, "split": "test", "role": f"test@{old}"},
        {"version_id": old, "split": "train", "role": f"train@{old}"},
    ]
    assert report["params"]["against_version_ids"] == [old]
    pairs = (await client.get(f"/api/v1/versions/{subject}/leakage/pairs")).json()
    assert pairs["total"] == 2  # not the old version's own OLD_INTERNAL pair


async def test_a_single_split_version_can_be_checked_against_others(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    """A-RS (one split, ``reserve_unseen_both``) refused ``no_splits``; against others it runs."""
    with sync_session_factory()() as db:
        reserve = make_version(
            db, _table([("social security number ssn", "reserve_unseen_both", "c1")]), ROLES
        )
        _, old = _versions(db)
    alone = await client.post(f"/api/v1/versions/{reserve.id}/leakage", json={})
    assert alone.status_code == 422 and alone.json()["error"]["code"] == "no_splits"
    response = await client.post(
        f"/api/v1/versions/{reserve.id}/leakage", json={"against_version_ids": [old]}
    )
    assert response.status_code == 200, response.text
    assert response.json()["report"]["result"]["near_pairs"] == {
        f"reserve_unseen_both|test@{old}": 1
    }


async def test_an_earlier_report_keeps_its_identity_and_the_against_one_is_its_own(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    with sync_session_factory()() as db:
        subject, old = _versions(db)
    plain = await client.post(f"/api/v1/versions/{subject}/leakage", json={})
    against = await client.post(
        f"/api/v1/versions/{subject}/leakage", json={"against_version_ids": [old]}
    )
    assert plain.status_code == against.status_code == 200
    assert "against_version_ids" not in plain.json()["report"]["params"]
    assert plain.json()["report"]["id"] != against.json()["report"]["id"]
    again = await client.post(
        f"/api/v1/versions/{subject}/leakage", json={"against_version_ids": [old]}
    )
    assert again.json()["outcome"] == "existing"
    with sync_session_factory()() as db:
        assert db.query(VersionReport).filter(VersionReport.kind == "leakage").count() == 2
    assert leakage_service.leakage_params(None, None) == plain.json()["report"]["params"]


async def test_refusals_name_what_to_change(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    with sync_session_factory()() as db:
        subject, old = _versions(db)
    cases = [
        ({"against_version_ids": [subject]}, "against_self"),
        (
            {"against_version_ids": [old], "inputs": [{"version_id": old}]},
            "against_with_inputs",
        ),
    ]
    for body, code in cases:
        response = await client.post(f"/api/v1/versions/{subject}/leakage", json=body)
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == code
    # the version under check still refuses a column it lacks (a misspelling is not lenient)
    flipped = await client.post(
        f"/api/v1/versions/{old}/leakage",
        json={"against_version_ids": [subject], "group_column": "carrier_id"},
    )
    assert flipped.status_code == 422
    assert flipped.json()["error"]["code"] == "group_column_missing"
