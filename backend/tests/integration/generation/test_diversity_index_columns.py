"""The automatic diversity report on a version whose content holds an index (dry run, 2026-11-01).

SSN pilot versions have two content columns, ``idx`` (an integer) and ``text``. The report chose
``idx``, the first alphabetically, and every automatic report failed ``DIVERSITY_INCOMPARABLE``, so
every D-10 read "not measured" with no reason. These fixtures go through the real sweep, the real
job body and real parquet files; the index is on every side, as it is in production, so nothing
here passes by the column being absent.
"""

from __future__ import annotations

from typing import Any

import httpx
import pyarrow as pa
import pytest
from sqlalchemy import select, text

from src.core.config import get_settings
from src.core.database import sync_session_factory
from src.core.storage import resolve_under_data_dir
from src.models.generation import DiversityReport
from src.models.version import Version
from src.services.generation import diversity_service
from src.services.row_keys import compute_row_key
from src.workers import diversity_tasks
from tests.integration.generation.test_diversity_report import sentences
from tests.support.generation_fixtures import Gen, set_role
from tests.support.generation_fixtures import make_version as make_any_version

ROLES = {"idx": "content", "text": "content"}
BOUND = [{"kind": "generation_run", "id": "gr_fixture"}]


def make_version(table: pa.Table, **kwargs: Any) -> str:
    """A version of a dataset whose target names no default column: its content decides."""
    return make_any_version(table, target_type="detector", held_out=False, **kwargs)


def indexed(
    rows: list[tuple[str, str | None]], *, start: int = 1_000_001, generated: int = 0
) -> pa.Table:
    """Rows of (split, text) with an integer ``idx`` and every system column; the LAST ``generated``
    rows are generated. Keys hash both content columns, as a build does."""
    columns: dict[str, list[Any]] = {
        "idx": [],
        "text": [],
        "_dw_row_key": [],
        "_dw_occurrence": [],
        "_dw_split": [],
        "_dw_origin": [],
        "_dw_source_id": [],
        "_dw_source_locator": [],
        "_dw_parent_keys": [],
    }
    for i, (split, value) in enumerate(rows):
        row = {"idx": start + i, "text": value}
        columns["idx"].append(row["idx"])
        columns["text"].append(value)
        columns["_dw_row_key"].append(compute_row_key(row, sorted(ROLES)))
        columns["_dw_occurrence"].append(0)
        columns["_dw_split"].append(split)
        columns["_dw_origin"].append("generated" if i >= len(rows) - generated else "source")
        columns["_dw_source_id"].append(None)
        columns["_dw_source_locator"].append(None)
        columns["_dw_parent_keys"].append(None)
    schema = pa.schema(
        [
            ("idx", pa.int64()),
            ("text", pa.string()),
            ("_dw_row_key", pa.string()),
            ("_dw_occurrence", pa.int64()),
            ("_dw_split", pa.string()),
            ("_dw_origin", pa.string()),
            ("_dw_source_id", pa.string()),
            ("_dw_source_locator", pa.string()),
            ("_dw_parent_keys", pa.list_(pa.string())),
        ]
    )
    return pa.table(columns, schema=schema)


@pytest.fixture
def small(monkeypatch: pytest.MonkeyPatch, gen: Gen) -> Gen:
    settings = get_settings()
    monkeypatch.setattr(settings, "diversity_bootstrap_resamples", 200)
    monkeypatch.setattr(settings, "diversity_cluster_k", 8)
    set_role("embeddings", gen.base_url, "embed-model", protocol="openai_embeddings")
    return gen


def swept_report(version_id: str) -> tuple[dict[str, Any], DiversityReport]:
    """Run the Beat sweep and the job it queued; (the job's params and result, the report)."""
    (job_id,) = diversity_tasks.sweep()
    outcome = diversity_tasks.report_job(job_id)
    assert outcome["outcome"] == "completed", outcome
    with sync_session_factory()() as db:
        job = db.execute(
            text("SELECT params, status FROM dw_jobs WHERE id = :id"), {"id": job_id}
        ).one()
        report = db.execute(
            select(DiversityReport).where(DiversityReport.version_id == version_id)
        ).scalar_one()
        db.expunge(report)
    assert job.status == "completed"
    return dict(job.params), report


async def test_the_sweep_measures_the_text_never_the_index(
    client: httpx.AsyncClient, small: Gen
) -> None:
    reference = make_version(indexed([("train", t) for t in sentences(60, 1)]), roles=ROLES)
    child = make_version(
        indexed([("train", t) for t in sentences(60, 1) + sentences(30, 4)], generated=30),
        roles=ROLES,
        parent=reference,
        bindings=BOUND,
    )
    params, row = swept_report(child)
    assert params["column"] == "text" and row.column == "text"
    assert row.reference_version_id == reference
    assert row.figures["distinct_1"]["verdict"] != "not_measured"
    assert row.verdict != "not_measured", row.reason
    assert diversity_tasks.sweep() == []  # one report, once


async def test_the_index_is_not_in_what_the_report_clusters(
    client: httpx.AsyncClient, small: Gen
) -> None:
    """Six texts, each on many rows with its own idx. ``idx`` in the cluster text would make every
    row a distinct text, defeating the fit on distinct texts (``union_distinct_refit_v1``)."""
    pool = sentences(6, 2)
    reference = make_version(indexed([("train", pool[i % 6]) for i in range(60)]), roles=ROLES)
    child = make_version(
        indexed([("train", pool[i % 6]) for i in range(90)], generated=30),
        roles=ROLES,
        parent=reference,
        bindings=BOUND,
    )
    _, row = swept_report(child)
    assert row.clustering["largest_share"]["texts"] == 6
    assert row.method["content_not_text"] == ["idx"]  # the report's identity says so


async def test_a_carrier_plan_is_no_reference_and_the_report_says_so(
    client: httpx.AsyncClient, small: Gen
) -> None:
    """The nearest ancestor with no generated row is the plan: its text column is empty. On its
    empty side both controls would fail for "too few reference rows" and read ``invalid``."""
    plan = make_version(indexed([("train", None)] * 40), roles=ROLES)
    child = make_version(
        indexed([("train", t) for t in sentences(40, 3)], generated=40),
        roles=ROLES,
        parent=plan,
        bindings=BOUND,
    )
    _, row = swept_report(child)
    assert row.verdict == "not_measured" and row.reference_version_id is None
    assert "holds text in this column" in str(row.reason)
    assert row.checks == [] and row.figures == {}


async def test_splits_that_differ_end_in_not_measured_from_the_sweep_and_a_409_from_a_person(
    client: httpx.AsyncClient, small: Gen
) -> None:
    reference = make_version(indexed([("train", t) for t in sentences(40, 1)]), roles=ROLES)
    rows = [("train", t) for t in sentences(40, 1)] + [("validation", t) for t in sentences(9, 8)]
    child = make_version(
        indexed(rows + [("train", t) for t in sentences(10, 5)], generated=10),
        roles=ROLES,
        parent=reference,
        bindings=BOUND,
    )
    # A person asked: refused and named before any job (FR-007.42).
    refused = await client.post(f"/api/v1/versions/{child}/diversity", json={})
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "DIVERSITY_INCOMPARABLE"
    assert "splits" in refused.json()["error"]["details"]["reasons"]
    # No one is there to read the sweep's: a recorded answer, and never queued again.
    params, row = swept_report(child)
    assert params["column"] == "text"
    assert row.verdict == "not_measured" and row.reference_version_id is None
    assert "splits differ" in str(row.reason)
    assert row.method["incomparable"]["reasons"] == ["splits"]
    assert row.method["incomparable"]["reference_version_id"] == reference
    assert diversity_tasks.sweep() == []
    with sync_session_factory()() as db:
        statuses = db.execute(
            text("SELECT status FROM dw_jobs WHERE kind = 'diversity_report'")
        ).scalars()
        assert list(statuses) == ["completed"]


async def test_a_person_cannot_measure_an_integer_column(
    client: httpx.AsyncClient, small: Gen
) -> None:
    version = make_version(
        indexed([("train", t) for t in sentences(20, 1)], generated=20),
        roles=ROLES,
        bindings=BOUND,
    )
    response = await client.post(f"/api/v1/versions/{version}/diversity", json={"column": "idx"})
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "DIVERSITY_COLUMN_NOT_TEXT"


async def test_a_version_of_only_numbers_has_nothing_to_measure(
    client: httpx.AsyncClient, small: Gen
) -> None:
    table = indexed([("train", None)] * 10, generated=10).drop_columns(["text"])
    version = make_version(table, roles={"idx": "content"}, bindings=BOUND)
    response = await client.post(f"/api/v1/versions/{version}/diversity", json={})
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "NO_COLUMN"
    assert diversity_tasks.sweep() == []


async def test_one_unreadable_version_does_not_stop_the_sweep(
    client: httpx.AsyncClient, small: Gen
) -> None:
    """The default column now reads a file's schema. A version whose files are gone is skipped, and
    the next one still gets its report."""
    gone = make_version(
        indexed([("train", t) for t in sentences(20, 1)], generated=20),
        roles=ROLES,
        bindings=BOUND,
    )
    good = make_version(
        indexed([("train", t) for t in sentences(20, 2)], generated=20),
        roles=ROLES,
        bindings=BOUND,
    )
    with sync_session_factory()() as db:
        version = db.get(Version, gone)
        assert version is not None
        for split in version.splits:
            resolve_under_data_dir(str(split["path"])).unlink()
        needing = diversity_service.versions_needing_reports(db)
    assert needing == [(good, "text")]


async def test_a_job_on_a_column_the_version_lacks_fails_rather_than_reading_not_measured(
    client: httpx.AsyncClient, small: Gen
) -> None:
    """ "Not measured" is for two sides that cannot be compared. A column the version itself lacks
    is a wrong request, and nothing about the data would explain a report on it."""
    reference = make_version(indexed([("train", t) for t in sentences(20, 1)]), roles=ROLES)
    child = make_version(
        indexed([("train", t) for t in sentences(30, 1)], generated=10),
        roles=ROLES,
        parent=reference,
        bindings=BOUND,
    )
    with sync_session_factory()() as db:
        job_id = diversity_service.create_job(db, child, "absent", "Test Operator", "operator")
    assert diversity_tasks.report_job(job_id)["outcome"] == "failed"
    with sync_session_factory()() as db:
        job = db.execute(text("SELECT result FROM dw_jobs WHERE id = :id"), {"id": job_id}).one()
        assert job.result["error"]["code"] == "DIVERSITY_INCOMPARABLE"
        assert db.execute(select(DiversityReport)).first() is None
