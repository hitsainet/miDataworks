"""A number is not text to any report (dry run, 2026-11-01). ``idx``, an index source's row number,
is a CONTENT column because it gives the row its identity. Read as text it was one more word on
every row: in the leakage check it hid identical texts from each other, and these reports read it
the same way. Each test fails when its report reads the number."""

from __future__ import annotations

from pathlib import Path

import httpx
import numpy as np

from src.core.database import sync_session_factory
from src.models import VersionReport
from src.services.curation import api, audit_service, cluster_service, report_service
from src.services.curation.codes import ReportInput
from src.services.curation.kinds import compute_for
from src.services.curation.profile_service import build_profile
from src.services.curation.text_baseline import text_baseline
from src.services.duck import TEXT_CONTENT
from src.services.generation import diversity_service
from tests.fixtures.humor_pool import with_system_columns
from tests.support.curation_fixtures import make_version
from tests.support.version_fixtures import make_source

ROLES = {"idx": "content", "text": "content", "label": "metadata"}
WORDS = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima".split()


def _noise(i: int) -> str:
    rng = np.random.default_rng(i)
    return " ".join(rng.choice(WORDS, size=8).tolist())


def test_the_cluster_and_profile_text_leaves_the_number_out() -> None:
    table = with_system_columns([{"idx": 1234, "text": "the words"}, {"idx": 5678, "text": "more"}])
    assert cluster_service.texts_of(table, ["idx", "text"]) == ["the words", "more"]
    assert cluster_service.texts_of(table, ["idx"]) == ["", ""]


def test_the_profile_gives_a_number_no_text_length() -> None:
    rows = [{"idx": 10**i, "text": "the same words here"} for i in range(8)]
    profile = build_profile(with_system_columns(rows), ROLES, sample=False, seed=7)
    assert set(profile["figures"]["lengths"]["columns"]) == {"text"}


def test_the_length_band_does_not_read_the_digits() -> None:
    table = with_system_columns([{"idx": 10**i, "text": "same"} for i in range(12)])
    bands = audit_service._length_band(table, ["idx", "text"]).to_pylist()  # noqa: SLF001
    assert len(set(bands)) == 1, bands


def test_the_wording_baseline_cannot_learn_the_label_from_the_number() -> None:
    """The number gives the label away and the words do not: reading it scores near 1."""
    rows = [{"idx": 7000 + i % 2, "text": _noise(i)} for i in range(120)]
    table = with_system_columns(rows)
    y = np.array([i % 2 for i in range(120)], dtype=np.int64)
    result = text_baseline(table, ["idx", "text"], y, ["no", "yes"], folds=3, seed=7, max_rows=1000)
    assert result["computed"] is True and result["content_columns"] == ["text"]
    assert result["tfidf_logreg"] < 0.75, result


def test_a_reference_version_is_read_the_same_way(clean_db: None, data_dir: Path) -> None:
    rows = [{"idx": i, "text": _noise(i), "label": "yes" if i % 2 else "no"} for i in range(20)]
    with sync_session_factory()() as db:
        version = make_version(db, with_system_columns(rows), ROLES)
        _, _, _, content = audit_service._reference_rows(  # noqa: SLF001
            db, str(version.id), "label", ["no", "yes"]
        )
    assert content == ["text"]


def test_contamination_does_not_count_the_number_as_a_word(clean_db: None, data_dir: Path) -> None:
    """The row's own words are one short of a match, and its number is the benchmark's missing word."""
    words = "one two three four five six seven eight nine ten eleven twelve".split()
    bench = make_source(
        data_dir,
        {"test": [{"text": "7 " + " ".join(words)}]},
        repo_id="tasksource/humicroedit",
        commit="a" * 40,
    )
    rows = [{"idx": 7, "text": " ".join(words), "label": "x"}]
    with sync_session_factory()() as db:
        version = make_version(db, with_system_columns(rows), ROLES)
    _, report = report_service.find_or_run_inline(
        "contamination",
        [ReportInput(str(version.id))],
        {"benchmark_source_ids": [bench], "n": 13},
        7,
        compute_for("contamination"),
        started_by="t",
        origin="operator",
    )
    assert report.result["benchmarks"][0]["rows_overlapping"] == 0


# A report computed the old way must not be found as the current one.


def test_the_audit_is_a_new_report() -> None:
    assert api.audit_params("label")["content_read"] == TEXT_CONTENT


def test_the_diversity_reference_is_a_new_report(clean_db: None, data_dir: Path) -> None:
    rows = [{"idx": i, "text": _noise(i), "label": "x"} for i in range(30)]
    with sync_session_factory()() as db:
        version = make_version(db, with_system_columns(rows), ROLES)
    report_id, _ = diversity_service.reference_clusters(version, 7, 3, "t")
    with sync_session_factory()() as db:
        report = db.get(VersionReport, report_id)
        assert report is not None and report.params["content_read"] == TEXT_CONTENT


async def test_the_routes_ask_for_a_new_report(
    client: httpx.AsyncClient, operator_name: str, clean_db: None, data_dir: Path
) -> None:
    rows = [{"idx": i, "text": _noise(i), "label": "x"} for i in range(12)]
    bench = make_source(data_dir, {"test": [{"text": "a benchmark"}]}, commit="a" * 40)
    with sync_session_factory()() as db:
        version = make_version(db, with_system_columns(rows), ROLES)
    base = f"/api/v1/versions/{version.id}"
    assert (await client.post(f"{base}/profile", json={})).status_code < 300
    answer = await client.post(f"{base}/contamination", json={"benchmark_source_ids": [bench]})
    assert answer.status_code < 300, answer.text
    with sync_session_factory()() as db:
        reports = {r.kind: r.params for r in db.query(VersionReport).all()}
    assert reports["contamination"]["content_read"] == TEXT_CONTENT
    assert reports["profile"]["content_read"] == TEXT_CONTENT
