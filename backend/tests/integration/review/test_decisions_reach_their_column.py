"""A review decision replaces the column that answers its question, and no other (dry run, 2026-11-02).

The operator's 300 calibration answers to "presented as a particular person's SSN?" stand on the rows
of the training version, whose label is ``deployed_truth``, derived from the plan: masked is false there
by decision, and "yes, presented" is the right answer to the other question. The resolver finds decisions
by the question of the run bound to the rows, and every reader applied them to whichever column it had
been given. A boolean column crashed the set check on ``no``. A text column would have taken the answers
silently, into training labels and off the rows flagged.

Each reader runs for real: the set check's counts, a publish build's files and an export's files. The
``label`` column is the control: there the same decisions must still apply.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.core.database import sync_session_factory
from src.core.errors import AppError
from src.core.storage import publish_dir
from src.models.version import Version
from src.operators import registry as registry_module
from src.services.calibration.constants import RUN_LABEL_COLUMN
from src.services.detector_sets.set_service import projected_label_counts
from src.services.exports import export_service
from src.services.publishing import feature_seams
from src.services.publishing.projection import (
    Counters,
    ProjectionError,
    apply_effective_labels,
    projection_spec,
)
from src.services.review import effective_label
from tests.support.calibration_fixtures import make_run, make_version, row_key
from tests.support.publish_fixtures import PublishDriver, completed_build, publisher
from tests.support.version_fixtures import driver

__all__ = ["driver", "publisher"]


def _rows() -> list[dict[str, Any]]:
    # Every other column disagrees with the decisions below, so an applied override shows.
    return [
        {"text": f"row number {i}", "label": "humorous", "truth": True, "other_answer": "humorous"}
        for i in range(6)
    ]


def _labelled(data_dir: Path) -> tuple[str, str, list[str]]:
    keys = [row_key(r["text"]) for r in _rows()]
    run = make_run(make_version(data_dir, _rows()), dict.fromkeys(keys, 0.9))
    bound = make_version(data_dir, _rows(), bindings=[{"kind": "label_run", "id": run.id}])
    return bound, run.id, keys


async def _decide(client: httpx.AsyncClient, run_id: str, keys: list[str]) -> None:
    """An operator's override on ``keys[0]`` and flag on ``keys[1]``, for the run's question."""
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


def _counts(version_id: str, column: str) -> dict[str, int]:
    with sync_session_factory()() as db:
        version = db.get(Version, version_id)
        assert version is not None
        return projected_label_counts(db, version, "train", column)


async def test_the_set_check_counts_read_another_answer_as_it_stands(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    version, run_id, keys = _labelled(data_dir)
    await _decide(client, run_id, keys)
    # the crash: a boolean column and a text label
    assert _counts(version, "truth") == {"true": 6}
    # the silent one: a text column would have taken the label and lost the flagged row
    assert _counts(version, "other_answer") == {"humorous": 6}
    # the control: the run's own column still takes both
    assert _counts(version, "label") == {"humorous": 4, "not_humorous": 1}


async def test_a_publish_build_ships_another_answer_unchanged_and_the_run_column_changed(
    client: httpx.AsyncClient, operator_name: str, publisher: PublishDriver, data_dir: Path
) -> None:
    version, run_id, keys = _labelled(data_dir)
    await _decide(client, run_id, keys)

    other = await completed_build(client, publisher, version, label_column="other_answer")
    assert other["omitted"]["overrides_applied"] == 0
    assert other["omitted"]["flagged_unresolved"] == 0
    table = pq.read_table(publish_dir(other["id"]) / other["files"][0]["path"])
    assert table.num_rows == 6 and set(table.column("other_answer").to_pylist()) == {"humorous"}

    own = await completed_build(client, publisher, version, label_column="label")
    assert own["omitted"]["overrides_applied"] == 1
    assert own["omitted"]["flagged_unresolved"] == 1
    assert pq.read_table(publish_dir(own["id"]) / own["files"][0]["path"]).num_rows == 5


async def test_an_export_hands_another_answer_no_decisions_and_the_run_column_its_own(
    client: httpx.AsyncClient,
    operator_name: str,
    publisher: PublishDriver,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Up to the projection, for real. These fixture versions name no source, which a finished export's
    manifest refuses, so the test reads what the reader hands the projection."""
    rows = [
        {**r, "prompt": f"prompt {i}", "completion": f"completion {i}", "label": True}
        for i, r in enumerate(_rows())
    ]
    keys = [row_key(r["text"]) for r in rows]
    run = make_run(make_version(data_dir, rows), dict.fromkeys(keys, 0.9))
    version = make_version(data_dir, rows, bindings=[{"kind": "label_run", "id": run.id}])
    await _decide(client, run.id, keys)
    handed: dict[str, Any] = {}
    real = export_service.write_split

    def spy(*args: Any, **kwargs: Any) -> Any:
        handed[args[6]] = args[7]
        return real(*args, **kwargs)

    monkeypatch.setattr(export_service, "write_split", spy)

    for column in ("other_answer", "label"):
        response = await client.post(
            "/api/v1/exports",
            json={
                "target": "trl",
                "trl_type": "kto",
                "version_id": version,
                "label_column": column,
            },
        )
        assert response.status_code == 202, response.text
        with contextlib.suppress(Exception):
            publisher.run_publish_tasks()

    assert handed["other_answer"] is None
    assert handed["label"][keys[0]]["state"] == "overridden"
    assert handed["label"][keys[1]]["state"] == "flagged_unresolved"


async def test_a_repair_of_a_labelled_version_keeps_the_same_reach(
    client: httpx.AsyncClient, data_dir: Path
) -> None:
    version, _, _ = _labelled(data_dir)
    assert effective_label.reaches_column(version, "label") is True
    assert effective_label.reaches_column(version, "truth") is False
    assert feature_seams.decisions_reach_column(version, "label") is True
    assert feature_seams.decisions_reach_column(version, "truth") is False


def test_with_no_run_the_decisions_were_made_about_this_versions_own_labels(
    client: httpx.AsyncClient, data_dir: Path
) -> None:
    plain = make_version(data_dir, _rows())
    assert effective_label.reaches_column(plain, "other_answer") is True


def test_the_run_column_is_the_one_threshold_labeler_writes() -> None:
    """Read from the live registry: rename the output and this names the rule that goes with it."""
    info = registry_module.current().get("threshold_labeler", "1")
    assert next(iter(info.output_columns)) == RUN_LABEL_COLUMN


def test_a_build_made_under_the_old_reach_is_not_reused() -> None:
    assert projection_spec("v", "label", review_layer=True)["review_reach"] == "run_label_column"


def test_a_label_the_column_cannot_hold_is_refused_by_name() -> None:
    """Where the rule does let a decision through, a mismatch still says what is wrong."""
    batch = pa.RecordBatch.from_pydict({"_dw_row_key": ["k"], "truth": [True]})
    resolved = {"k": {"label": "no", "state": "overridden", "decision_id": "d"}}
    with pytest.raises(ProjectionError) as caught:
        apply_effective_labels(batch, "truth", resolved, Counters())
    assert caught.value.code == "override_label_type"
    assert caught.value.details["column"] == "truth"


async def test_the_set_check_answers_that_refusal_with_its_name_not_a_crash(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version, run_id, keys = _labelled(data_dir)
    await _decide(client, run_id, keys)
    monkeypatch.setattr(feature_seams, "decisions_reach_column", lambda *_: True)
    with pytest.raises(AppError) as caught:
        _counts(version, "truth")
    assert caught.value.code == "override_label_type"
