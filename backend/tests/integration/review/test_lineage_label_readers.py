"""Readers of a version's label runs follow the lineage (dry run, 2026-11-01).

A later build (a repair, a re-split) binds nothing itself, so reading only ``version.bindings``
misses the run that labelled its rows. Which part of the lineage counts differs per reader:

- the effective label, read by every publish build and export, takes the NEAREST labeller on each
  path: a repair keeps the human decisions on its rows, while a relabel does not inherit decisions
  on an older question;
- the audit's default run is that same nearest labeller.

The reward mark's whole-lineage reading is pinned in
``tests/integration/detector_sets/test_export_reward_marks.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pyarrow.parquet as pq

from src.core.database import sync_session_factory
from src.core.storage import publish_dir
from src.models.version import Version, VersionInput
from src.services.version_lineage import nearest_label_runs
from tests.support.calibration_fixtures import make_run, make_version, row_key
from tests.support.publish_fixtures import PublishDriver, completed_build, publisher
from tests.support.version_fixtures import driver

__all__ = ["driver", "publisher"]

OTHER_QUESTION = "Does this text name a place?"


def _rows(n: int = 6) -> list[dict[str, Any]]:
    # the label column disagrees with the decisions below, so an applied override shows
    return [{"text": f"row number {i}", "label": "humorous"} for i in range(n)]


def _child(data_dir: Path, parent: str, **kwargs: Any) -> str:
    """A build of ``parent`` with the same rows: the way production records it, one version input.
    With no ``bindings`` it is a repair or re-split, which binds nothing itself."""
    child = make_version(data_dir, _rows(), **kwargs)
    with sync_session_factory()() as db:
        db.add(VersionInput(version_id=child, position=0, kind="version", input_version_id=parent))
        db.commit()
    return child


async def _decide(client: httpx.AsyncClient, run_id: str, keys: list[str]) -> None:
    """A human override on ``keys[0]`` and a human flag on ``keys[1]``, for ``run_id``'s question."""
    queue = await client.post(
        "/api/v1/review-queues",
        json={"kind": "label_review", "label_run_id": run_id, "row_keys": keys[:2]},
    )
    assert queue.status_code == 201, queue.text
    page = await client.get(f"/api/v1/review-queues/{queue.json()['id']}/items")
    items = {i["row_key"]: i["id"] for i in page.json()["items"]}
    decide = "/api/v1/review-items/{}/decisions"
    override = await client.post(
        decide.format(items[keys[0]]),
        json={"decision": "override", "override_label": "not_humorous", "reason": "flat"},
    )
    flag = await client.post(
        decide.format(items[keys[1]]), json={"decision": "flag", "reason": "unsure"}
    )
    assert override.status_code == 201 and flag.status_code == 201, (override.text, flag.text)


def _labelled(data_dir: Path) -> tuple[str, str, list[str]]:
    """(version binding a run, the run, the row keys)."""
    plain = make_version(data_dir, _rows())
    keys = [row_key(r["text"]) for r in _rows()]
    run = make_run(plain, dict.fromkeys(keys, 0.9))
    bound = make_version(data_dir, _rows(), bindings=[{"kind": "label_run", "id": run.id}])
    return bound, run.id, keys


def _published_labels(build: dict[str, Any]) -> tuple[dict[str, str], int, int]:
    """(row key -> shipped label, overrides applied, flagged rows omitted), read from the real build
    output files."""
    root = publish_dir(build["id"])
    labels: dict[str, str] = {}
    for f in build["files"]:
        table = pq.read_table(root / f["path"])
        labels.update(
            zip(
                table.column("_dw_row_key").to_pylist(),
                table.column("label").to_pylist(),
                strict=True,
            )
        )
    omitted = build["omitted"]
    return labels, omitted["overrides_applied"], omitted["flagged_unresolved"]


async def test_a_repair_build_publishes_the_human_decisions_made_on_its_ancestor(
    client: httpx.AsyncClient,
    operator_name: str,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    labelled, run_id, keys = _labelled(data_dir)
    await _decide(client, run_id, keys)
    repaired = _child(data_dir, labelled)
    build = await completed_build(client, publisher, repaired)
    labels, overrides, omitted = _published_labels(build)
    assert overrides == 1 and omitted == 1
    assert labels[keys[0]] == "not_humorous"  # the operator's override reached the published file
    assert keys[1] not in labels  # the flagged row is not published
    assert set(labels) == set(keys) - {keys[1]}
    assert labels[keys[2]] == "humorous"


async def test_a_relabel_does_not_inherit_decisions_on_an_older_question(
    client: httpx.AsyncClient,
    operator_name: str,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    """The reason the walk stops at the nearest labeller: the row keys are the same, the decisions
    answer another question, and the new run's label must stand."""
    labelled, run_id, keys = _labelled(data_dir)
    await _decide(client, run_id, keys)
    second = make_run(labelled, dict.fromkeys(keys, 0.9), question=OTHER_QUESTION)
    relabelled = _child(data_dir, labelled, bindings=[{"kind": "label_run", "id": second.id}])
    build = await completed_build(client, publisher, relabelled)
    labels, overrides, omitted = _published_labels(build)
    assert overrides == 0 and omitted == 0
    assert set(labels) == set(keys) and labels[keys[0]] == "humorous"


def test_each_input_of_a_merge_answers_for_itself(
    client: httpx.AsyncClient, data_dir: Path
) -> None:
    """Two labelled inputs both count; what lies behind a labeller does not."""
    oldest, old_run, keys = _labelled(data_dir)
    run_a = make_run(oldest, dict.fromkeys(keys, 0.9), question=OTHER_QUESTION)
    run_b = make_run(oldest, dict.fromkeys(keys, 0.1), question="A third question?")
    a = _child(data_dir, oldest, bindings=[{"kind": "label_run", "id": run_a.id}])
    b = make_version(data_dir, _rows(), bindings=[{"kind": "label_run", "id": run_b.id}])
    bare = _child(data_dir, b)
    merged = _child(data_dir, a)
    with sync_session_factory()() as db:
        db.add(VersionInput(version_id=merged, position=1, kind="version", input_version_id=bare))
        db.commit()
        version = db.get(Version, merged)
        assert version is not None
        assert nearest_label_runs(db, version) == [run_a.id, run_b.id]
        assert old_run not in nearest_label_runs(db, version)


async def test_an_audit_of_a_repair_build_defaults_to_the_ancestors_run(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    rows = [{"text": f"audited row {i}"} for i in range(120)]
    plain = make_version(data_dir, rows)
    keys = [row_key(r["text"]) for r in rows]
    run = make_run(plain, {k: (i % 10) / 10 + 0.05 for i, k in enumerate(keys)})
    labelled = make_version(data_dir, rows, bindings=[{"kind": "label_run", "id": run.id}])
    repaired = make_version(data_dir, rows)
    with sync_session_factory()() as db:
        db.add(
            VersionInput(version_id=repaired, position=0, kind="version", input_version_id=labelled)
        )
        db.commit()
    response = await client.post(f"/api/v1/versions/{repaired}/audit", json={"size": 60, "seed": 3})
    assert response.status_code == 201, response.text
    status = response.json()
    assert status["strata"]["label_run_id"] == run.id
    items = (await client.get(f"/api/v1/review-queues/{status['queue_id']}/items?limit=200")).json()
    # the strata and snapshots are the run's, not "unlabeled"
    assert len(items["items"]) == 60
    assert all(not i["stratum"].startswith("unlabeled") for i in items["items"])


async def test_an_audit_with_no_labeller_anywhere_is_still_refused(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    """The control: the walk finds the run; with none in the lineage the refusal stands."""
    rows = [{"text": f"unlabelled row {i}"} for i in range(120)]
    parent = make_version(data_dir, rows)
    child = make_version(data_dir, rows)
    with sync_session_factory()() as db:
        db.add(VersionInput(version_id=child, position=0, kind="version", input_version_id=parent))
        db.commit()
    response = await client.post(f"/api/v1/versions/{child}/audit", json={"size": 60})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "AUDIT_NEEDS_QUESTION"
