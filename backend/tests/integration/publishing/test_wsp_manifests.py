"""WSP marks at the build sites and in every manifest (021 FTASKS 7.x; FR-021.9, FR-021.10).

Real builds (the production registry), the publish worker's real task bodies against the fake Hub,
and the real export. ``shipped_wsp`` is the only producer of a manifest's ``wsp``; the publish
build and the export each call it once per split (R1A-D8: a spy in real runs, not an AST scan).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.core.storage import export_dir
from src.schemas.dataset_version import PACKAGED_SCHEMA_PATH
from src.services import wsp_partitions
from src.services.publishing import feature_seams
from src.services.publishing.manifest_builder import MANIFEST_FILE, rows_content
from tests.integration.publishing.test_generated_held_out_caveat import publisher
from tests.integration.test_directed_held_out import driver
from tests.integration.test_version_build import request, setup, src
from tests.support.operator_build_driver import RealDriver
from tests.support.publish_fixtures import (
    PublishDriver,
    completed_build,
    store_token,
)
from tests.support.version_fixtures import make_source

__all__ = ["driver", "publisher"]

THREE = {
    "operator": "split",
    "version": "1.3.0",
    "params": {
        "split_names": ["fit", "test", "hold"],
        "held_out": ["test", "hold"],
        "mode": "keep_source",
        "wsp_roles": ["wsp_practice", "none", "wsp_heldout"],
        "wsp_set_kinds": ["in_distribution", "none", "long_untruncated"],
        "wsp_min_chars": [0, 0, 8],
        "wsp_text_column": "body",
    },
}
ROWS = {
    "fit": [{"body": f"beta fit row {i}" + "!" * i, "label": "neg"} for i in range(5)],
    "test": [{"body": f"beta test row {i}", "label": "pos"} for i in range(3)],
    "hold": [{"body": f"beta held-out row {i}" + "?" * (3 * i), "label": "pos"} for i in range(4)],
}


async def beta(
    client: httpx.AsyncClient,
    driver: RealDriver,
    data_dir: Path,
    name: str,
    column: str = "body",
) -> str:
    """wsp-beta: text column ``body`` (not ``text``), splits fit/test/hold (021-D23). With
    ``column="text"`` the same rows under ``text``, which a miForge corpus export ships."""
    from src.core.database import sync_session_factory
    from src.models import Job

    rows = {
        split: [{column: r["body"], "label": r["label"]} for r in items]
        for split, items in ROWS.items()
    }
    step = json.loads(json.dumps(THREE))
    step["params"]["wsp_text_column"] = column
    source = make_source(data_dir, rows, repo_id=f"t/{name}", text_columns=(column,))
    ds, rev = await setup(client, {"format": "dw.recipe/v1", "steps": [step]}, name=name)
    response = await request(
        client, ds, rev, [src(source)], seed=7, column_roles={column: "content"}
    )
    assert response.status_code == 202, response.text
    status = driver.run(response.json()["job_id"])
    with sync_session_factory()() as db:
        job = db.get(Job, response.json()["job_id"])
        assert job is not None and job.result is not None
        assert status == "completed", job.result
        return str(job.result["version_id"])


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[str, Path]]]:
    calls: list[tuple[str, Path]] = []
    real = wsp_partitions.shipped_wsp

    def recording(version_split: Any, shipped_path: Path) -> Any:
        calls.append((str(version_split["name"]), Path(shipped_path)))
        return real(version_split, shipped_path)

    monkeypatch.setattr(wsp_partitions, "shipped_wsp", recording)
    yield calls


async def export_corpus(
    client: httpx.AsyncClient, publisher: PublishDriver, version_id: str
) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/exports",
        json={
            "target": "miforge_set",
            "miforge_set_kind": "corpus",
            "version_id": version_id,
            "label_column": "label",
        },
    )
    assert response.status_code == 202, response.text
    publisher.run_publish_tasks()
    row: dict[str, Any] = (
        await client.get(f"/api/v1/exports/{response.json()['export_id']}")
    ).json()
    return row


async def test_shipped_wsp_called_once_per_split(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: RealDriver,
    publisher: PublishDriver,
    data_dir: Path,
    spy: list[tuple[str, Path]],
) -> None:
    """FR-021.10 (R1A-D8, M-20): a real publish build and a real export of a three-split version
    (two marked) each call ``shipped_wsp`` exactly three times, with each split and the path of
    the file that ships."""
    await store_token(client)
    version_id = await beta(client, driver, data_dir, "spy", column="text")
    build = await completed_build(client, publisher, version_id)
    assert sorted(name for name, _ in spy) == ["fit", "hold", "test"]
    shipped = {f["name"]: f for f in build["files"]}
    for name, path in spy:
        assert path.name == Path(shipped[name]["path"]).name and path.exists(), name
    spy.clear()
    row = await export_corpus(client, publisher, version_id)
    assert row["status"] == "completed", row
    assert sorted(name for name, _ in spy) == ["fit", "hold", "test"]
    for _, path in spy:
        assert path.parent == export_dir(row["id"]) and path.exists()


async def test_shipped_wsp_key_set(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: RealDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    """R2B-10 (M-47): the manifest's ``wsp`` keys are exactly the schema's ``WspPartition``
    properties, in the draft and the export; ``version_length`` never ships."""
    schema = json.loads(PACKAGED_SCHEMA_PATH.read_bytes())
    expected = set(schema["$defs"]["WspPartition"]["properties"])
    assert expected == {"role", "set_kind", "text_column", "min_chars", "length"}
    await store_token(client)
    version_id = await beta(client, driver, data_dir, "keys", column="text")
    await completed_build(client, publisher, version_id)
    draft = (await client.get(f"/api/v1/versions/{version_id}/handoff-manifest")).json()
    row = await export_corpus(client, publisher, version_id)
    exported = json.loads((export_dir(row["id"]) / MANIFEST_FILE).read_bytes())
    for manifest in (draft, exported):
        marks = {s["name"]: s.get("wsp") for s in manifest["content"]["splits"]}
        assert marks["test"] is None
        for name in ("fit", "hold"):
            assert set(marks[name]) == expected, name
        assert marks["hold"]["length"]["unit"] == "characters"


async def test_a_projection_that_omits_the_shortest_row_changes_length_min(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: RealDriver,
    publisher: PublishDriver,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-021.10: ``length`` describes the SHIPPED rows; ``version_length`` the version's."""
    import pyarrow.parquet as pq

    from src.core.storage import resolve_under_data_dir
    from tests.integration.test_wsp_versions import by_name

    await store_token(client)
    version_id = await beta(client, driver, data_dir, "omit")
    stored = by_name(version_id)["hold"]
    table = pq.read_table(resolve_under_data_dir(stored["path"]), columns=["_dw_row_key", "body"])
    rows = sorted(
        zip(table.column(1).to_pylist(), table.column(0).to_pylist(), strict=True),
        key=lambda r: len(r[0]),
    )
    shortest_key = rows[0][1]
    monkeypatch.setattr(feature_seams, "effective_labels_available", lambda: True)
    monkeypatch.setattr(feature_seams, "decisions_reach_column", lambda version, column: True)
    monkeypatch.setattr(
        feature_seams,
        "resolve_effective_labels",
        lambda version, question, run, keys: {
            shortest_key: {"state": "flagged_unresolved", "label": None, "decision_id": "d1"}
        },
    )
    build = await completed_build(client, publisher, version_id)
    hold = {f["name"]: f for f in build["files"]}["hold"]
    assert stored["wsp"]["version_length"]["min"] == len(rows[0][0])
    assert hold["wsp"]["length"]["min"] == len(rows[1][0]) > len(rows[0][0])


async def test_an_empty_shipped_wsp_split_is_refused(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: RealDriver,
    publisher: PublishDriver,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-021.9: a projection that leaves a WSP split no rows fails the build by its code."""
    await store_token(client)
    version_id = await beta(client, driver, data_dir, "emptied")
    monkeypatch.setattr(feature_seams, "effective_labels_available", lambda: True)
    monkeypatch.setattr(feature_seams, "decisions_reach_column", lambda version, column: True)
    monkeypatch.setattr(
        feature_seams,
        "resolve_effective_labels",
        lambda version, question, run, keys: {
            k: {"state": "flagged_unresolved", "label": None, "decision_id": "d1"} for k in keys
        },
    )
    response = await client.post(
        f"/api/v1/versions/{version_id}/publish-builds", json={"label_column": "label"}
    )
    assert response.status_code in (200, 202), response.text
    publisher.run_publish_tasks()
    got = (await client.get(f"/api/v1/publish-builds/{response.json()['build_id']}")).json()
    assert got["status"] == "failed", got
    assert got["error"]["code"] == "wsp_partition_empty", got["error"]


def test_build_predates_wsp() -> None:
    """FR-021.10, R1A-D17 (M-21): a marked version whose build files carry no ``wsp`` key at all
    is refused; an unmarked split of a marked version (``wsp: None``) is not stale."""
    from src.core.errors import AppError

    base = {
        "path": "data/x.parquet",
        "rows": 1,
        "bytes": 1,
        "sha256": "a" * 64,
        "logical_digest": "b" * 64,
        "label_counts": {},
        "held_out": False,
        "evaluation_only": False,
    }
    kw: dict[str, Any] = {
        "columns": [],
        "label_column": None,
        "omitted": {"excluded": 0, "flagged_unresolved": 0, "overrides_applied": 0},
        "labelers": [],
        "calibration": [],
    }
    marks = {"fit": {"role": "wsp_practice"}}
    old_build = [{**base, "name": "fit"}, {**base, "name": "test"}]
    with pytest.raises(AppError) as caught:
        rows_content(build_files=old_build, version_marks=marks, **kw)
    assert caught.value.code == "build_predates_wsp"
    assert caught.value.details == {"splits": ["fit", "test"]}
    mark = {
        "role": "wsp_practice",
        "set_kind": "in_distribution",
        "text_column": "body",
        "min_chars": 0,
        "length": {"unit": "characters", "min": 1, "max": 2, "median": 1},
    }
    new_build = [{**base, "name": "fit", "wsp": mark}, {**base, "name": "test", "wsp": None}]
    content = rows_content(build_files=new_build, version_marks=marks, **kw)
    assert [s["wsp"] for s in content["splits"]] == [mark, None]
    # a version with no marks reads an old build as unmarked (nothing is fabricated)
    assert [
        s["wsp"] for s in rows_content(build_files=old_build, version_marks={}, **kw)["splits"]
    ] == [None, None]


async def test_an_export_that_drops_the_text_column_is_refused(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: RealDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    """OVN-wsp-dataworks-4: an export ships only its target's columns; a mark whose text column
    does not ship cannot state lengths, and dropping the mark would let a consumer read a held-out
    partition as unmarked, so the export is refused by name."""
    source_rows = {
        split: [{"text": r["body"], "body": r["body"], "label": r["label"]} for r in items]
        for split, items in ROWS.items()
    }
    from src.core.database import sync_session_factory
    from src.models import Job

    source = make_source(data_dir, source_rows, repo_id="t/drops", text_columns=("text", "body"))
    ds, rev = await setup(client, {"format": "dw.recipe/v1", "steps": [THREE]}, name="drops")
    response = await request(
        client, ds, rev, [src(source)], seed=7, column_roles={"text": "content", "body": "content"}
    )
    assert response.status_code == 202, response.text
    assert driver.run(response.json()["job_id"]) == "completed"
    with sync_session_factory()() as db:
        job = db.get(Job, response.json()["job_id"])
        assert job is not None and job.result is not None
        version_id = str(job.result["version_id"])
    row = await export_corpus(client, publisher, version_id)
    assert row["status"] == "failed", row
    assert (row.get("error") or {}).get("code") == "wsp_text_column_invalid", row
