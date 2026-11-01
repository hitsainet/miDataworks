"""``CalibrationStatus`` is field for field 008's ``CalibrationRef`` minus ``labeler_fingerprint``
(006 FTASKS 11.4; FR-006.38). Read from both schema modules, never a copied list."""

from __future__ import annotations

from typing import get_args

from src.models.enums import GateRule, GateVerdict
from src.schemas import calibration as ours
from src.schemas import dataset_version as theirs


def literals(model: type, field: str) -> set[str]:
    annotation = model.model_fields[field].annotation  # type: ignore[attr-defined]
    values: set[str] = set()
    stack = [annotation]
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            values.add(node)
        else:
            stack.extend(get_args(node))
    return values


def test_field_names_match() -> None:
    assert set(ours.CalibrationStatus.model_fields) == set(theirs.CalibrationRef.model_fields) - {
        "labeler_fingerprint"
    }
    assert set(ours.AurocRef.model_fields) == set(theirs.Auroc.model_fields)
    assert set(ours.CalibrationSetRef.model_fields) == set(theirs.CalibrationSetRef.model_fields)


def test_enum_values_match_008() -> None:
    assert literals(ours.CalibrationStatus, "verdict") == literals(theirs.CalibrationRef, "verdict")
    assert literals(ours.CalibrationStatus, "rule") == literals(theirs.CalibrationRef, "rule")
    assert {v.value for v in GateVerdict} == literals(theirs.CalibrationRef, "verdict")
    assert {r.value for r in GateRule} == literals(theirs.CalibrationRef, "rule")
    assert literals(ours.CalibrationSetRef, "licence_class") == literals(
        theirs.CalibrationSetRef, "licence_class"
    )


def test_rows_are_never_shipped() -> None:
    ref = ours.CalibrationSetRef(id="cs_1", licence_class="private_only")
    assert ref.rows_shipped is False
    theirs.CalibrationSetRef.model_validate(ref.model_dump())
