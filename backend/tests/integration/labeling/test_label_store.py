"""The idempotent chunk commit (005 FTASKS 9.1; FR-005.32). Written after mutation control C12
(the commit-marker check) survived the suite: nothing exercised a second commit of one chunk."""

from __future__ import annotations

import httpx
from sqlalchemy import func, select

from src.core.clock import utc_now
from src.core.database import sync_session_factory
from src.models.label import Label
from src.models.label_run import LabelRun, LabelRunChunk
from src.services import label_store
from src.services.label_store import LabelRecord
from tests.integration.labeling.helpers import setup_classifier, start_body
from tests.support.labeling_fixtures import Labeling


async def test_a_chunk_is_committed_once_whatever_calls_it(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    version_id, template_id = await setup_classifier(client, labeling, n=3)
    run_id = (
        await client.post("/api/v1/label-runs", json=start_body(version_id, template_id))
    ).json()["id"]
    records = [
        LabelRecord(
            row_key=f"{i:064x}",
            outcome="positive",
            parsed_value={},
            probability=0.9,
            distribution={"false": 0.1, "true": 0.9},
            raw_output={},
            rationale=None,
            steering_state="unsteered (scoring mode)",
            latency_ms=1,
            skip_reason=None,
            scored_at=utc_now(),
        )
        for i in range(5)
    ]
    path, sha = label_store.stage_chunk(run_id, 0, records)
    with sync_session_factory()() as db:
        run = db.get(LabelRun, run_id)
        assert run is not None
        assert label_store.commit_chunk(db, run, 0, "job_a", path, sha, records) is True
        # a second commit of the same chunk (a recovery racing a live worker) writes nothing
        assert label_store.commit_chunk(db, run, 0, "job_b", path, sha, records) is False
        assert label_store.recover_uncommitted(db, run, "job_c") == []
        labels = db.execute(
            select(func.count()).select_from(Label).where(Label.label_run_id == run_id)
        ).scalar_one()
        markers = (
            db.execute(select(LabelRunChunk).where(LabelRunChunk.label_run_id == run_id))
            .scalars()
            .all()
        )
    assert labels == 5
    assert [(m.chunk_index, m.job_id, m.row_count) for m in markers] == [(0, "job_a", 5)]
