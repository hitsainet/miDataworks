"""The ``shortcut_audit`` report operator through 003's real executor (FTASKS 4.7) and the warning
copy (FTASKS 5.5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.operators.errors import StepFailed
from src.services.curation.audit_service import audit_table
from src.services.curation.warning_copy import warning_copy
from tests.fixtures import humor_pool as hp
from tests.support.curation_fixtures import run_operator


def test_report_operator_touches_no_row_and_reports_the_audit(data_dir: Path) -> None:
    table = hp.candidates()
    sink: list[dict] = []
    ran = run_operator(
        "shortcut_audit",
        {"label_column": "label", "exclude_columns": ["label_probability"]},
        table,
        hp.ROLES,
        report_sink=sink,
    )
    assert ran.output.num_rows == table.num_rows and ran.events.num_rows == 0
    assert ran.result.rows_dropped == 0 and ran.result.rows_kept == table.num_rows
    by = {c["column"]: c for c in sink[0]["columns"]}
    assert by["format"]["figure"] == pytest.approx(0.885, abs=0.005)
    assert "label_probability" not in by


def test_report_operator_refuses_without_the_label_column(data_dir: Path) -> None:
    with pytest.raises(StepFailed) as exc:
        run_operator("shortcut_audit", {"label_column": "missing"}, hp.candidates(), hp.ROLES)
    assert exc.value.code == "no_label_column"


def test_warning_copy_matches_the_mockup_facts() -> None:
    result = audit_table(hp.candidates(), hp.ROLES, "label", seed=hp.SEED)
    fmt = next(c for c in result["columns"] if c["column"] == "format")
    text = warning_copy("format", fmt, fmt["figure"])
    assert text.startswith("Format predicts the label 88.5% of the time")
    assert "chance is 50%" in text and "on 10,914 rows" in text
    assert "Humorous is 89% joke" in text and "not humorous is 88% headline" in text
    assert "build the format-balanced version" in text
