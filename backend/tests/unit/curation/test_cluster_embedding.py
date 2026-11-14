"""``cluster_embedding`` (004 FR-004.24 embedding basis; FTASKS 15.5), CPU only.

The fake server's vectors depend only on a text's FIRST word (``vector_for``), so the embedding
clusters are "by first word" while the words that follow are disjoint: a lexical clustering cannot
produce that grouping by accident, so a figure computed on the wrong basis fails here."""

from __future__ import annotations

from collections import Counter
from typing import Any

from tests.support.curation_fixtures import run_operator
from tests.unit.curation import test_dedup_embedding as dedup_tests
from tests.unit.curation.test_dedup_embedding import KEY, ROLES, FakeEmbeddings, Leases, table

fake = dedup_tests.fake  # the fake OpenAI-compatible server and lease manager fixtures
leases = dedup_tests.leases

HEADS = ["alpha", "bravo", "charlie"]
TEXTS = [f"{HEADS[i % 3]} {' '.join(f'w{i}x{j}' for j in range(6))}" for i in range(30)]


def clusters(params: dict[str, Any], data: Any) -> tuple[Any, dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    ran = run_operator("cluster_embedding", params, data, ROLES, report_sink=reports)
    assert len(reports) == 1
    return ran, reports[0]


def test_clusters_follow_the_embeddings(fake: FakeEmbeddings, leases: Leases) -> None:
    ran, report = clusters({"k": 3}, table(TEXTS))
    assert ran.output.num_rows == len(TEXTS)  # a report step keeps every row
    assert report["basis"] == "embedding" and report["k"] == 3
    assert report["sizes"] == [10, 10, 10]
    heads = {e["excerpt"].split()[0] for e in report["exemplars"]}
    assert heads == set(HEADS)
    assert report["embedding"]["model"] == "embed-1" and KEY not in str(report)
    assert leases.acquired == 1 and report["pinned"] is True
    sent = [t for c in fake.calls for t in c["body"]["input"]]
    assert Counter(sent) == Counter(TEXTS)
    headers = {k.lower(): v for k, v in fake.calls[0]["headers"].items()}
    assert headers["x-millm-load-policy"] == "refuse"


def test_blank_rows_are_reported_unclustered_never_sent(
    fake: FakeEmbeddings, leases: Leases
) -> None:
    _, report = clusters({"k": 3}, table([*TEXTS, "", "  "]))
    assert report["rows_clustered"] == len(TEXTS)
    assert report["rows_unclustered"] == {"over_input_cap": 0, "empty_input": 2}


def test_the_same_seed_gives_the_same_clusters(fake: FakeEmbeddings, leases: Leases) -> None:
    _, a = clusters({"k": 3}, table(TEXTS))
    _, b = clusters({"k": 3}, table(TEXTS))
    assert a["exemplars"] == b["exemplars"] and b["rows_from_cache"] == len(TEXTS)


def test_the_report_reaches_the_steps_meta_json_without_a_sink(
    fake: FakeEmbeddings, leases: Leases
) -> None:
    """live2 finding (2026-10-18): the executor kept only ``split_roles`` from a report, so in
    production this operator's whole output (sizes, exemplars) was stored nowhere. The executor
    now writes every report to meta.json (and step_ingest stores it, migration 0026); this reads
    it the way ingest does, with no monkeypatched sink."""
    ran = run_operator("cluster_embedding", {"k": 3}, table(TEXTS), ROLES)
    (report,) = ran.meta["reports"]
    assert report["basis"] == "embedding" and report["sizes"] == [10, 10, 10]
    assert {e["excerpt"].split()[0] for e in report["exemplars"]} == set(HEADS)
    assert all(type(s) is int for s in report["sizes"])  # NumPy integers made plain JSON
