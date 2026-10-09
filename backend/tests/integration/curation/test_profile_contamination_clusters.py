"""Profile, contamination, clusters and cluster assignment (FTASKS 10.1–10.8)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

from src.core.database import sync_session_factory
from src.models import VersionReport
from src.operators.errors import StepFailed
from src.services.curation import api, contamination_service, report_service
from src.services.curation.codes import ReportInput
from src.services.curation.errors import CurationError
from src.services.curation.kinds import compute_for
from src.services.curation.profile_service import build_profile
from tests.fixtures.humor_pool import with_system_columns
from tests.support.curation_fixtures import make_version, run_operator
from tests.support.version_fixtures import make_source

ROLES = {"text": "content", "topic": "metadata"}
BENCH_ITEM = (
    "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen"
)
TOPICS = {
    "cooking": "pasta sauce garlic oven recipe dinner kitchen bake",
    "sport": "goal match team score league season coach player",
}


def _rows(n: int = 40) -> list[dict[str, Any]]:
    out = []
    for i in range(n):
        topic = "cooking" if i % 2 else "sport"
        words = TOPICS[topic].split()
        out.append(
            {"text": f"{i} " + " ".join(words[(i + j) % 8] for j in range(8)), "topic": topic}
        )
    return out


def _report(kind: str, version_id: str, params: dict[str, Any]) -> VersionReport:
    _, report = report_service.find_or_run_inline(
        kind,
        [ReportInput(version_id)],
        params,
        7,
        compute_for(kind),
        started_by="t",
        origin="operator",
    )
    return report


class TestProfile:
    def test_honest_absence_never_zero(self) -> None:
        table = with_system_columns(_rows(10))
        profile = build_profile(table, ROLES, sample=True, seed=1)
        f = profile["figures"]
        for name in ("language", "tokens", "near_duplicates_embedding", "contamination"):
            assert f[name]["status"] == "not_computed", name
            assert f[name]["reason"] and "value" not in f[name] and "rows" not in f[name], name
        assert f["counts"]["status"] == "computed" and f["counts"]["sample"] is True
        assert f["counts"]["n_rows"] == 10
        assert f["lengths"]["columns"]["text"]["characters"]["unit"] == "characters"
        assert f["exact_duplicates"]["groups"] == 0 and f["clusters"]["basis"] == "lexical"

    def test_profile_report_with_sample_and_no_label(self, clean_db: None, data_dir: Path) -> None:
        with sync_session_factory()() as db:
            v = make_version(db, with_system_columns(_rows(40)), ROLES)
        report = _report("profile", str(v.id), {"sample_size": 10})
        figures = report.result["figures"]
        assert report.result["sample"] is True and figures["counts"]["n_rows"] == 10
        assert figures["shortcut_audit"]["status"] == "not_computed"

    def test_profile_operator(self, data_dir: Path) -> None:
        sink: list[dict] = []
        ran = run_operator("profile", {}, with_system_columns(_rows(12)), ROLES, report_sink=sink)
        assert ran.events.num_rows == 0 and sink[0]["figures"]["counts"]["n_rows"] == 12


def _bench(data_dir: Path, commit: str = "a" * 40) -> str:
    return make_source(
        data_dir,
        {"test": [{"text": BENCH_ITEM}, {"text": "unrelated benchmark item"}]},
        repo_id="tasksource/humicroedit",
        commit=commit,
    )


class TestContamination:
    def test_catalogue_lists_humicroedit_pinned_and_import_state(
        self, clean_db: None, data_dir: Path
    ) -> None:
        with sync_session_factory()() as db:
            before = contamination_service.catalogue(db)
        assert before[0]["revision"] == "f5a16e65b0854032ab9b4c82ca182a6381b83bd5"
        assert before[0]["imported"] is False
        _bench(data_dir, "f5a16e65b0854032ab9b4c82ca182a6381b83bd5")
        with sync_session_factory()() as db:
            assert contamination_service.catalogue(db)[0]["imported"] is True

    def test_overlap_report_names_benchmark_revision_item(
        self, clean_db: None, data_dir: Path
    ) -> None:
        bench = _bench(data_dir)
        rows = _rows(10) + [{"text": "prefix " + BENCH_ITEM + " suffix", "topic": "x"}]
        with sync_session_factory()() as db:
            v = make_version(db, with_system_columns(rows), ROLES)
        report = _report("contamination", str(v.id), {"benchmark_source_ids": [bench], "n": 13})
        result = report.result["benchmarks"][0]
        assert result["revision"] == "a" * 40 and result["rows_overlapping"] == 1
        assert result["rows"][0]["item"] == f"tasksource/humicroedit@{'a' * 40}:0"

    def test_decontaminate_drops_and_refuses_another_revision(
        self, clean_db: None, data_dir: Path
    ) -> None:
        bench = _bench(data_dir)
        rows = _rows(4) + [{"text": BENCH_ITEM, "topic": "x"}]
        table = with_system_columns(rows)
        ran = run_operator(
            "decontaminate",
            {"benchmark_source_id": bench, "benchmark_revision": "a" * 40, "threshold": 0.5},
            table,
            ROLES,
        )
        dropped = ran.events_by_reason("benchmark_overlap")
        assert len(dropped) == 1 and dropped[0]["statistic_name"] == "ngram_overlap"
        assert dropped[0]["statistic_text"] == f"tasksource/humicroedit@{'a' * 40}:0"
        with pytest.raises(StepFailed) as exc:
            run_operator(
                "decontaminate",
                {"benchmark_source_id": bench, "benchmark_revision": "b" * 40},
                table,
                ROLES,
            )
        assert exc.value.code == "benchmark_revision_unavailable"


class TestClusters:
    def test_cluster_report_records_basis_and_assignment_reads_the_artefact(
        self, clean_db: None, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with sync_session_factory()() as db:
            v = make_version(db, with_system_columns(_rows(40)), ROLES)
        report = _report("clusters", str(v.id), {"k": 2})
        result = report.result
        assert result["basis"] == "lexical" and result["k"] == 2
        assert sorted(result["sizes"]) == [20, 20] and result["vectoriser"]["n_features"] == 2**15
        from src.services.curation import cluster_service

        def no_refit(*a: Any, **k: Any) -> Any:
            raise AssertionError("assign_to_clusters must not refit")

        monkeypatch.setattr(cluster_service, "fit", no_refit)
        own = api.assign_to_clusters(report.id, [r["text"] for r in _rows(40)])
        clusters = {i % 2: set() for i in range(2)}
        for i, a in enumerate(own):
            clusters[i % 2].add(a["cluster"])
        assert all(len(s) == 1 for s in clusters.values()) and clusters[0] != clusters[1]
        other = api.assign_to_clusters(report.id, ["garlic pasta oven", "team goal coach"])
        assert {o["cluster"] for o in other} == clusters[1] | clusters[0]
        assert all(o["distance"] >= 0 for o in other)

    def test_embedding_report_is_refused(self, clean_db: None, data_dir: Path) -> None:
        with sync_session_factory()() as db:
            v = make_version(db, with_system_columns(_rows(10)), ROLES)
        report = _report("clusters", str(v.id), {"k": 2})
        fake = VersionReport(
            **{c.name: getattr(report, c.name) for c in VersionReport.__table__.columns}
        )
        fake.id = str(uuid.uuid4())
        fake.params_hash = "e" * 64
        fake.result = {**report.result, "basis": "embedding"}
        with sync_session_factory()() as db:
            db.add(fake)
            db.commit()
        with pytest.raises(CurationError) as exc:
            api.assign_to_clusters(fake.id, ["x"])
        assert exc.value.code == "basis_unreproducible"

    def test_cluster_balancer_caps_clusters(self, data_dir: Path) -> None:
        rows = _rows(40)
        table = with_system_columns(rows)
        ran = run_operator("cluster_balancer", {"k": 2, "cap": 5}, table, ROLES)
        assert ran.output.num_rows == 10
        dropped = ran.events_by_reason("cluster_cap")
        assert len(dropped) == 30 and dropped[0]["statistic_name"] == "cluster_size"
        topics = (
            pa.Table.from_pylist(ran.output.select(["topic"]).to_pylist())
            .column("topic")
            .to_pylist()
        )
        assert sorted(topics).count("sport") == 5
