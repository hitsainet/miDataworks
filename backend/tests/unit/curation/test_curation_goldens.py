"""The prototype's reference figures, rebuilt from the committed records (FTDD 004 §10.3; FTASKS
3.2, 3.3, 4.8, 6.5, 7.5).

The fixture's embedded counts are compared with the record files first, so drift fails loudly.
"""

from __future__ import annotations

import pytest

from src.services.curation.api import evaluate_audit
from src.services.curation.audit_service import audit_table
from src.services.curation.level_service import EffectiveLevel
from src.services.curation.shortcut_rules import DEFAULT_SHORTCUT_MARGIN_PP
from tests.fixtures import humor_pool as hp

DERIVED = {"label_probability": "threshold_labeler@1"}
DEFAULT = EffectiveLevel(float(DEFAULT_SHORTCUT_MARGIN_PP), "code_default", None, None, None)


@pytest.mark.skipif(
    not (hp.RECORDS / "labelling_run.json").exists(),
    reason=(
        "records/ is not in this checkout: the public mirror strips it (sync-to-clean "
        "EXCLUDED_PATHS), so the fixture-vs-records drift check runs on the private repository only"
    ),
)
def test_humor_pool_matches_records() -> None:
    records = hp.read_records()
    assert hp.CANDIDATE_COUNTS == records["run"]["train"]["by_source_and_label"]
    assert sum(hp.CANDIDATE_COUNTS.values()) == records["run"]["train"]["rows"] == 10_914
    crosstab = records["run"]["crosstab_source_by_label"]
    pool = {
        f"{source}|{label}": n
        for sample in crosstab.values()
        for source, labels in sample.items()
        for label, n in labels.items()
        if label != "excluded"
    }
    assert pool == hp.POOL_COUNTS
    prep = records["prep"]["humor-jev9b"]
    assert prep["test_cells"] == hp.PUBLISH_TEST_CELLS
    assert (prep["train"], prep["test"]) == (hp.PUBLISH_TRAIN, hp.PUBLISH_TEST)
    assert records["balanced"]["per_cell"] == hp.FORMAT_PER_CELL
    assert (records["balanced"]["train"], records["balanced"]["test"]) == (
        hp.FORMAT_TRAIN,
        hp.FORMAT_TEST,
    )


def test_fixture_texts_do_not_encode_the_label() -> None:
    table = hp.candidates()
    assert table.num_rows == 10_914
    result = audit_table(table, hp.ROLES, "label", derived=DERIVED, seed=hp.SEED)
    length = next(c for c in result["columns"] if c["column"] == "length_band")
    assert abs(length["figure"] - 0.5) < 0.03


@pytest.fixture(scope="module")
def candidate_audit() -> dict:
    return audit_table(hp.candidates(), hp.ROLES, "label", derived=DERIVED, seed=hp.SEED)


def test_humor_pool_format_warns(candidate_audit: dict) -> None:
    by = {c["column"]: c for c in candidate_audit["columns"]}
    for column in ("source_label", "format"):
        assert by[column]["figure"] == pytest.approx(0.885, abs=0.005)
        assert by[column]["n_rows"] == 10_914 and by[column]["chance"] == 0.5
        assert by[column]["valid"]
    warnings, _, invalid = evaluate_audit("v", candidate_audit, DEFAULT)
    assert {w.column for w in warnings} == {"source_label", "format"}
    assert invalid == []


def test_id_column_reads_chance(candidate_audit: dict) -> None:
    by = {c["column"]: c for c in candidate_audit["columns"]}
    assert by["id"]["n_values"] == 10_914
    assert abs(by["id"]["figure"] - 0.5) < 0.01 and by["id"]["valid"]
    warnings, _, _ = evaluate_audit("v", candidate_audit, DEFAULT)
    assert "id" not in {w.column for w in warnings}


def test_probability_column_excluded(candidate_audit: dict) -> None:
    audited = {c["column"] for c in candidate_audit["columns"]}
    assert "label_probability" not in audited
    excluded = {e["column"]: e for e in candidate_audit["excluded_columns"]}
    assert excluded["label_probability"]["reason"] == "label_derived"
    assert excluded["label_probability"]["source_operator"] == "threshold_labeler@1"


def test_every_permuted_control_reads_chance(candidate_audit: dict) -> None:
    for column in candidate_audit["columns"]:
        assert abs(column["control_mean"] - 0.5) < 0.02, column["column"]
