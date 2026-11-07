"""The agreement report end to end, and its disagreements sent to review (suite tracker 1.7;
006 FTASKS 11.6; FR-009.52 - FR-009.56, FR-006.40).

Before 2026-10-16 no test called ``POST /agreement-reports``: the report and its
``send_disagreements_to_review`` path were reachable only by the route list. The fixture here is
built so the expected disagreement set is known by construction from DIFFERENT inputs than the
code reads: the probe and judge agree everywhere except on rows whose index is a multiple of 7.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from src.core.database import sync_session_factory
from src.models.review import ReviewItem, ReviewQueue
from tests.support.calibration_fixtures import make_run, make_version, row_key

N = 70
FLIP = {i for i in range(N) if i % 7 == 0}


def _rows() -> list[dict[str, Any]]:
    return [{"text": f"agreement row {i}", "human": "yes" if i % 2 else "no"} for i in range(N)]


@pytest.fixture
def setup(data_dir: Path) -> dict[str, Any]:
    rows = _rows()
    version = make_version(data_dir, rows)
    keys = [row_key(r["text"]) for r in rows]
    probe_out = {k: ("positive" if i % 2 else "negative") for i, k in enumerate(keys)}
    judge_out = {
        k: (("negative" if v == "positive" else "positive") if i in FLIP else v)
        for i, (k, v) in enumerate(probe_out.items())
    }
    probe = make_run(
        version,
        {k: (0.9 if v == "positive" else 0.1) for k, v in probe_out.items()},
        outcomes=probe_out,
        model_id="probe-model",
    )
    judge = make_run(
        version,
        {k: (0.85 if v == "positive" else 0.15) for k, v in judge_out.items()},
        outcomes=judge_out,
        model_id="judge-model",
    )
    return {
        "version": version,
        "probe": probe.id,
        "judge": judge.id,
        "flipped": sorted(keys[i] for i in FLIP),
    }


def body(s: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "version_id": s["version"],
        "split": "train",
        "probe_label_run_id": s["probe"],
        "judge_label_run_id": s["judge"],
        "reference": {
            "kind": "label_column",
            "column": "human",
            "positive_values": ["yes"],
            "negative_values": ["no"],
        },
        **extra,
    }


async def test_the_report_names_exactly_the_rows_probe_and_judge_disagree_on(
    client: httpx.AsyncClient, operator_name: str, setup: dict[str, Any]
) -> None:
    r = await client.post("/api/v1/agreement-reports", json=body(setup))
    assert r.status_code == 201, r.text
    figures = r.json()["figures"]
    assert figures["rows_compared"] == N
    assert sorted(figures["disagreements"]) == setup["flipped"]
    assert figures["agreement"] == round(1 - len(FLIP) / N, 4)
    assert r.json()["review_queue_id"] is None
    with sync_session_factory()() as db:
        assert db.execute(select(ReviewQueue)).scalars().all() == []


async def test_disagreements_open_one_label_review_queue_over_exactly_those_rows(
    client: httpx.AsyncClient, operator_name: str, setup: dict[str, Any]
) -> None:
    r = await client.post(
        "/api/v1/agreement-reports", json=body(setup, send_disagreements_to_review=True)
    )
    assert r.status_code == 201, r.text
    queue_id = r.json()["review_queue_id"]
    assert queue_id
    with sync_session_factory()() as db:
        queues = db.execute(select(ReviewQueue)).scalars().all()
        assert [q.id for q in queues] == [queue_id]
        queue = queues[0]
        assert queue.kind == "label_review" and queue.label_run_id == setup["judge"]
        assert str(queue.version_id) == setup["version"]
        assert queue.created_by == operator_name and queue.sample_spec == {
            "source": "row_keys",
            "count": len(FLIP),
        }
        keys = sorted(
            db.execute(select(ReviewItem.row_key).where(ReviewItem.queue_id == queue_id)).scalars()
        )
    assert keys == setup["flipped"]
    back = (await client.get(f"/api/v1/agreement-reports/{r.json()['id']}")).json()
    assert back["review_queue_id"] == queue_id


async def test_the_report_is_computed_off_the_greenlet(
    client: httpx.AsyncClient,
    operator_name: str,
    setup: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``prepare`` bootstraps AUROCs with numpy, so it does not run on ``run_sync``."""
    from greenlet import getcurrent

    from src.services.detector_sets import agreement

    calls: list[tuple[str, bool]] = []
    real = agreement.prepare

    def spy(session: Any, data: dict[str, Any], who: Any) -> Any:
        calls.append((data["version_id"], getcurrent().parent is None))
        return real(session, data, who)

    monkeypatch.setattr(agreement, "prepare", spy)
    r = await client.post("/api/v1/agreement-reports", json=body(setup))
    assert r.status_code == 201, r.text
    assert calls == [(setup["version"], True)]
    assert (await client.get(f"/api/v1/agreement-reports/{r.json()['id']}")).status_code == 200


async def test_the_teacher_caveat_reads_the_training_lineage(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, setup: dict[str, Any]
) -> None:
    """A repair or re-split binds nothing itself; the judge is still the rows' teacher."""
    from src.models.version import VersionInput

    labelled = make_version(
        data_dir,
        [{"text": "teacher row", "human": "yes"}],
        bindings=[{"kind": "label_run", "id": setup["judge"]}],
    )
    repaired = make_version(data_dir, [{"text": "repaired row", "human": "yes"}])
    unrelated = make_version(data_dir, [{"text": "unrelated row", "human": "yes"}])
    with sync_session_factory()() as db:
        db.add(
            VersionInput(version_id=repaired, position=0, kind="version", input_version_id=labelled)
        )
        db.commit()

    async def caveat_for(training: str) -> Any:
        r = await client.post(
            "/api/v1/agreement-reports", json=body(setup, training_version_id=training)
        )
        assert r.status_code == 201, r.text
        return r.json()["figures"]["caveat"]

    assert await caveat_for(repaired)
    assert await caveat_for(unrelated) is None  # the control: the walk is what decides
