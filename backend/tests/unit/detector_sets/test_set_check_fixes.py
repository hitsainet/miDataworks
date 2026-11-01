"""Pure rules behind the three detector-set check fixes of 2026-10-07.

The integration side (004's real audit on a Humicroedit-shaped version, REST, the snapshot) is in
``tests/integration/detector_sets/test_set_checks_humicroedit.py``.
"""

from __future__ import annotations

import pyarrow as pa

from src.services.curation import api as curation_api
from src.services.curation.audit_service import audit_table, audited_and_excluded
from src.services.detector_sets import checks

from .test_pure_rules import inputs

# --- defect 1: declared label sources ---------------------------------------------------------


def test_a_declared_source_is_excluded_with_its_reason_and_an_undeclared_one_audited() -> None:
    columns = ["text", "human_label", "meanGrade", "grades", "kind", "_dw_row_key"]
    roles = dict.fromkeys(columns, "metadata") | {"text": "content"}
    audited, excluded = audited_and_excluded(
        columns, roles, "human_label", {}, label_sources={"meanGrade": "set - training rows"}
    )
    assert "meanGrade" not in audited
    assert "grades" in audited and "kind" in audited
    assert {
        "column": "meanGrade",
        "reason": "label_source",
        "declared_by": "set - training rows",
    } in (excluded)
    # nothing declared: the column is audited, as before
    audited, _ = audited_and_excluded(columns, roles, "human_label", {})
    assert "meanGrade" in audited


def test_the_audit_document_reports_the_declared_source() -> None:
    n = 40
    table = pa.table(
        {
            "label": ["a", "b"] * (n // 2),
            "source": [1.0, 0.0] * (n // 2),
            "other": ["x"] * n,
        }
    )
    roles = {"label": "metadata", "source": "metadata", "other": "metadata"}
    plain = audit_table(table, roles, "label", seed=1)
    assert "source" in {c["column"] for c in plain["columns"]}
    declared = audit_table(table, roles, "label", seed=1, label_sources={"source": "set - train"})
    assert "source" not in {c["column"] for c in declared["columns"]}
    assert {"column": "source", "reason": "label_source", "declared_by": "set - train"} in (
        declared["excluded_columns"]
    )


def test_audit_params_keep_their_hash_when_nothing_is_declared() -> None:
    """A report stored before ``label_sources`` existed must still be found (same hash)."""
    assert "label_sources" not in curation_api.audit_params("label")
    assert curation_api.audit_params("label", {}) == curation_api.audit_params("label")
    with_sources = curation_api.audit_params("label", {"b": "r2", "a": "r1"})
    assert with_sources["label_sources"] == {"a": "r1", "b": "r2"}


def test_d3_reports_exclusions_when_green_and_when_refused() -> None:
    excluded = (
        {"column": "meanGrade", "reason": "label_source", "declared_by": "s - training rows"},
        {"column": "label_probability", "reason": "label_derived", "source_operator": "t@1"},
    )
    green = next(
        o
        for o in checks.evaluate(inputs(warnings=checks.WarningFacts(excluded=excluded)))
        if o.code == "D-3"
    )
    assert green.outcome == "green"
    assert green.details["excluded"] == [dict(e) for e in excluded]
    assert "'meanGrade'" in green.reason and "'label_probability'" in green.reason
    warning = {"column": "grades", "figure": 0.99, "level": 10.0, "level_source": "code_default"}
    refused = next(
        o
        for o in checks.evaluate(
            inputs(warnings=checks.WarningFacts(warnings=(warning,), excluded=excluded[:1]))
        )
        if o.code == "D-3"
    )
    assert refused.outcome == "refused"
    assert refused.details["excluded"] == [dict(excluded[0])]
    assert "'meanGrade'" in refused.reason
    # nothing excluded: no exclusion sentence
    plain = next(o for o in checks.evaluate(inputs()) if o.code == "D-3")
    assert "Not audited" not in plain.reason


# --- defect 2: a human-labelled basis -----------------------------------------------------------

HUMAN = {
    "kind": "human_labelled",
    "label_column": "human_label",
    "negative_values": ["0"],
    "labelled_by": "five graders",
    "rule": "meanGrade <= 0.4",
}


def _d8(basis: dict[str, object] | None) -> checks.CheckOutcome:
    return next(o for o in checks.evaluate(inputs(negatives_basis=basis)) if o.code == "D-8")


def test_d8_reports_human_labels_without_the_assumed_caveat() -> None:
    d8 = _d8(HUMAN)
    assert d8.outcome == "green"
    assert "human-labelled" in d8.reason and "five graders" in d8.reason
    assert "'human_label'" in d8.reason and "'0'" in d8.reason
    assert "assumed" not in d8.reason.lower() and "caveat" not in d8.reason.lower()
    assert d8.details["basis"] == HUMAN
    assumed = _d8({"kind": "assumed_negative"})
    assert assumed.outcome == "note" and "caveat" in assumed.reason
    filtered = _d8({"kind": "labeler_filtered", "labeler_identity_hash": "a" * 64, "rule": "p"})
    assert filtered.outcome == "green" and "labeler" in filtered.reason
    assert "human" not in filtered.reason


def test_the_request_model_holds_each_basis_to_its_own_fields() -> None:
    import pytest
    from pydantic import ValidationError

    from src.schemas.detector_sets import NegativesBasis

    assert NegativesBasis(**HUMAN).kind == "human_labelled"  # type: ignore[arg-type]
    for broken in (
        {k: v for k, v in HUMAN.items() if k != "labelled_by"},
        {k: v for k, v in HUMAN.items() if k != "label_column"},
        {**HUMAN, "negative_values": []},
        {k: v for k, v in HUMAN.items() if k != "rule"},
        {**HUMAN, "labeler_identity_hash": "a" * 64},
        {"kind": "assumed_negative", "labelled_by": "someone"},
        {
            "kind": "labeler_filtered",
            "labeler_identity_hash": "a" * 64,
            "rule": "p",
            "label_column": "x",
        },
    ):
        with pytest.raises(ValidationError):
            NegativesBasis(**broken)  # type: ignore[arg-type]


# --- defect 3: null labels ------------------------------------------------------------------------


def test_a_null_label_is_a_value_the_mapping_must_name() -> None:
    from src.services.detector_sets import label_rules

    counts = {"0": 6, "1": 4, "None": 3}
    out = label_rules.check_mapping("id_test", counts, {"0": "negative", "1": "positive"})
    assert [(p.code, p.value) for p in out] == [("unmapped_value", "None")]
    assert "Null label values" in out[0].message and "'None'" in out[0].message
    for key in ("None", "null"):
        mapping = {"0": "negative", "1": "positive", key: "excluded"}
        assert label_rules.check_mapping("id_test", counts, mapping) == []
        assert label_rules.expected_counts(counts, mapping) == {
            "positive": 4,
            "negative": 6,
            "excluded": 3,
        }
    # "None" wins over "null", as miStudio's map_label reads it first
    both = {"0": "negative", "1": "positive", "None": "excluded", "null": "negative"}
    assert label_rules.expected_counts(counts, both)["excluded"] == 3
    # calibration negatives must map it too
    cal = label_rules.check_mapping(
        "calibration_negatives", counts, {"0": "negative", "1": "excluded"}
    )
    assert [(p.code, p.value) for p in cal] == [("unmapped_value", "None")]
    # a literal "null" label string is its own value, not a null
    literal = label_rules.check_mapping(
        "train", {"null": 2, "1": 2, "0": 2}, {"None": "excluded", "1": "positive", "0": "negative"}
    )
    assert [(p.code, p.value) for p in literal] == [("unmapped_value", "null")]


def test_the_projection_counts_null_labels_apart() -> None:
    from src.services.publishing.projection import Counters, apply_effective_labels

    batch = pa.RecordBatch.from_pydict(
        {"_dw_row_key": ["a", "b", "c", "d"], "label": [1, None, 0, None]}
    )
    plain = Counters()
    apply_effective_labels(batch, "label", None, plain)
    assert plain.label_counts == {"1": 1, "0": 1} and plain.null_labels == 2
    reviewed = Counters()
    resolved = {"c": {"state": "overridden", "label": None, "decision_id": "x"}}
    apply_effective_labels(batch, "label", resolved, reviewed)
    assert reviewed.label_counts == {"1": 1} and reviewed.null_labels == 3


def test_the_counts_check_compares_excluded_where_reported() -> None:
    from src.workers.detector_send_tasks import counts_differ

    expected = {"positive": 541, "negative": 1800, "excluded": 1714}
    assert counts_differ(dict(expected), expected) == []
    assert counts_differ({**expected, "excluded": 0}, expected) == ["excluded"]
    assert counts_differ({"positive": 541, "negative": 1800}, expected) == []
    assert counts_differ({**expected, "positive": 540}, expected) == ["positive"]
    assert counts_differ({"negative": 1800, "excluded": 1714}, expected) == ["positive"]


def test_an_omitted_excluded_count_is_not_read_as_zero() -> None:
    """C35 survived first time: the integration fixture's expected excluded was 0, so reading an
    omitted ``excluded`` as 0 agreed with it by construction."""
    from src.workers.detector_send_tasks import counts_differ, registered_counts

    expected = {"positive": 541, "negative": 1800, "excluded": 1714}
    older = registered_counts({"positive": 541, "negative": 1800, "kinds": {}})
    assert older == {"positive": 541, "negative": 1800}
    assert counts_differ(older, expected) == []
    newer = registered_counts({"positive": 541, "negative": 1800, "excluded": 0})
    assert counts_differ(newer, expected) == ["excluded"]


def test_do_register_calls_both_count_decisions() -> None:
    """The registration step CALLS ``registered_counts`` and ``counts_differ`` (AST, not text)."""
    import ast
    import inspect

    from src.workers import detector_send_tasks

    tree = ast.parse(inspect.getsource(detector_send_tasks.do_register))
    called = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert {"registered_counts", "counts_differ"} <= called
    # and the registered counts come from that call, not from a comprehension beside it
    assigns = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "registered" for t in n.targets)
    ]
    assert len(assigns) == 1
    value = assigns[0].value
    assert isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
    assert value.func.id == "registered_counts"
