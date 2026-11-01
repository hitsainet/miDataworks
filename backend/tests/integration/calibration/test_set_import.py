"""Calibration sets from an imported version (006 FTASKS 6.1 – 6.3, 6.5, 6.6)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from src.core.database import sync_session_factory
from src.models.calibration import CalibrationSet, CalibrationSetLabel
from tests.support.calibration_fixtures import LABELS, MAPPING, QUESTION, humor_rows, make_version


def body(version_id: str, mapping: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "version_id": version_id,
        "question": QUESTION,
        "label_set": LABELS,
        "mapping": mapping or MAPPING,
    }


def set_count() -> int:
    with sync_session_factory()() as s:
        return int(s.execute(select(func.count()).select_from(CalibrationSet)).scalar_one())


@pytest.fixture
def version(client: httpx.AsyncClient, data_dir: Path) -> str:
    return make_version(data_dir, humor_rows(200))


async def test_preview_counts_and_writes_nothing(client: httpx.AsyncClient, version: str) -> None:
    response = await client.post("/api/v1/calibration-sets/preview", json=body(version))
    assert response.status_code == 200, response.text
    data = response.json()
    counts = data["counts"]
    assert counts["rows"] == 200 and counts["references"] == 50 and counts["groups"] == 50
    assert counts["labeled"] == counts["positives"] + counts["negatives"]
    assert counts["labeled"] + counts["excluded"] + counts["references"] == 200
    assert counts["rows_with_ratings"] == 150
    assert data["ratings_sorted"] is False
    assert set_count() == 0


async def test_sortedness_is_detected_and_warned(client: httpx.AsyncClient, data_dir: Path) -> None:
    version = make_version(data_dir, humor_rows(200, sorted_ratings=True))
    data = (await client.post("/api/v1/calibration-sets/preview", json=body(version))).json()
    assert data["ratings_sorted"] is True
    assert any("ranks, not rater identities" in w for w in data["warnings"])


async def test_import_creates_the_set_with_its_labels_in_version_order(
    client: httpx.AsyncClient, operator_name: str, version: str
) -> None:
    response = await client.post("/api/v1/calibration-sets/import", json=body(version))
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["licence_class"] == "private_only"  # no source recorded: never "permits"
    assert data["created_by"] == operator_name and data["source_kind"] == "imported"
    assert data["mapping"]["schema"] == "dw.calibration-mapping/v1"
    with sync_session_factory()() as s:
        labels = list(
            s.execute(
                select(CalibrationSetLabel)
                .where(CalibrationSetLabel.calibration_set_id == data["id"])
                .order_by(CalibrationSetLabel.position)
            ).scalars()
        )
    assert [x.position for x in labels] == list(range(200))
    assert labels[0].is_reference and labels[0].human_label is None
    edited = [x for x in labels if not x.is_reference and x.ratings]
    assert all(len(x.ratings or []) == 5 for x in edited)
    got = await client.get(f"/api/v1/calibration-sets/{data['id']}")
    assert got.status_code == 200 and got.json()["counts"] == data["counts"]
    listed = (await client.get(f"/api/v1/calibration-sets?version_id={version}")).json()
    assert listed["total"] == 1 and listed["items"][0]["id"] == data["id"]


async def test_the_same_mapping_twice_is_refused(
    client: httpx.AsyncClient, operator_name: str, version: str
) -> None:
    first = await client.post("/api/v1/calibration-sets/import", json=body(version))
    second = await client.post("/api/v1/calibration-sets/import", json=body(version))
    assert second.status_code == 409
    assert second.json()["error"]["details"]["calibration_set_id"] == first.json()["id"]


async def test_conflicting_duplicate_row_keys_are_refused(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    rows = humor_rows(40)
    rows[1] = {**rows[1], "meanGrade": 2.4, "_dw_row_key": "a" * 64}
    rows[2] = {**rows[2], "meanGrade": 0.2, "_dw_row_key": "a" * 64}
    version = make_version(data_dir, rows)
    response = await client.post("/api/v1/calibration-sets/import", json=body(version))
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "ROW_KEY_CONFLICT" and error["details"]["row_key"] == "a" * 64
    assert sorted(error["details"]["labels"]) == ["humorous", "not_humorous"]
    assert set_count() == 0


async def test_one_class_only_is_refused(
    client: httpx.AsyncClient, operator_name: str, version: str
) -> None:
    mapping = copy.deepcopy(MAPPING)
    mapping["human_label"]["positive_at_or_above"] = 9.0
    mapping["human_label"]["negative_at_or_below"] = 8.0
    response = await client.post("/api/v1/calibration-sets/import", json=body(version, mapping))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MAPPING_INVALID"
    assert "0 positives" in response.json()["error"]["message"]


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda m: m["human_label"].update(column="nope"), "MAPPING_INVALID"),
        (lambda m: m.update(group=None), "VALIDATION_ERROR"),  # reference without group
        (lambda m: m.update(extra=1), "VALIDATION_ERROR"),  # extra="forbid"
        (lambda m: m["ratings"].update(format="csv"), "VALIDATION_ERROR"),
        (lambda m: m["human_label"].update(negative_at_or_below=2.0), "VALIDATION_ERROR"),
        (lambda m: m.update(strata=["missing_col"]), "MAPPING_INVALID"),
    ],
)
async def test_each_invalid_mapping_shape_is_refused(
    client: httpx.AsyncClient, operator_name: str, version: str, mutate: Any, code: str
) -> None:
    mapping = copy.deepcopy(MAPPING)
    mutate(mapping)
    mapping = {k: v for k, v in mapping.items() if v is not None}
    response = await client.post("/api/v1/calibration-sets/import", json=body(version, mapping))
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == code
    assert set_count() == 0


async def test_a_content_column_cannot_be_the_human_label(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    version = make_version(data_dir, humor_rows(40), content=("text", "meanGrade"))
    response = await client.post("/api/v1/calibration-sets/import", json=body(version))
    assert response.status_code == 422
    assert "content column" in response.json()["error"]["message"]


async def test_import_needs_an_operator_name(client: httpx.AsyncClient, version: str) -> None:
    response = await client.post("/api/v1/calibration-sets/import", json=body(version))
    assert response.status_code == 422 and response.json()["error"]["code"] == "NO_IDENTITY"


async def test_an_unknown_version_is_404(client: httpx.AsyncClient, operator_name: str) -> None:
    response = await client.post("/api/v1/calibration-sets/preview", json=body("not-a-uuid"))
    assert response.status_code == 404
