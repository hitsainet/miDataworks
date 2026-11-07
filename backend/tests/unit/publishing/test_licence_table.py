"""The licence table v1 and classification (FR-008.58, FR-008.59; 008 FTASKS 3.1, 3.2)."""

from __future__ import annotations

import hashlib

import pytest

from src.services.publishing.licence_table import (
    CURRENT_TABLE_FILE,
    TABLE,
    AnnotationRef,
    LicenceClass,
    classify,
)

#: Pinned: v1 is frozen. A change is a new file (licence-table-v2.json), never an edit of this one.
V1_SHA256 = "7cb4ef5736c2f27d7ffa4cbb4fe717d414663427b21dd56ca6ab20e6bb82b531"


def test_the_v1_file_is_frozen() -> None:
    assert hashlib.sha256(CURRENT_TABLE_FILE.read_bytes()).hexdigest() == V1_SHA256


def test_v1_holds_the_accepted_list() -> None:
    assert TABLE.version == 1
    assert "cc-by-2.0" in TABLE.permits and "mit" in TABLE.permits
    assert "other" not in TABLE.permits and "cc-by-nc-4.0" not in TABLE.permits
    assert len(TABLE.permits) == 16


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("cc-by-2.0", LicenceClass.PERMITS),  # ColBERT
        ("CC-BY-2.0", LicenceClass.PERMITS),  # case-insensitive
        ("unknown", LicenceClass.PRIVATE_ONLY),  # Humicroedit
        (None, LicenceClass.PRIVATE_ONLY),  # offensive-humor
        ("", LicenceClass.PRIVATE_ONLY),
        ("other", LicenceClass.PRIVATE_ONLY),
        (["mit", "other"], LicenceClass.PRIVATE_ONLY),  # every element must permit
        (["mit", "apache-2.0"], LicenceClass.PERMITS),
        ([], LicenceClass.PRIVATE_ONLY),
        ("cc-by-nc-4.0", LicenceClass.PRIVATE_ONLY),
        ({"weird": 1}, LicenceClass.PRIVATE_ONLY),
    ],
)
def test_the_table_classifies(raw: object, expected: LicenceClass) -> None:
    result = classify(raw, [])
    assert result.licence_class is expected
    assert result.table_version == 1


def test_the_latest_annotation_wins_over_the_table() -> None:
    forbids = classify(
        "cc-by-2.0", [AnnotationRef("permits", "a1"), AnnotationRef("forbids", "a2")]
    )
    assert forbids.licence_class is LicenceClass.FORBIDS and forbids.annotation_id == "a2"
    permits = classify(None, [AnnotationRef("permits", "a3")])
    assert permits.licence_class is LicenceClass.PERMITS and permits.decided_by == "annotation"


def test_an_unknown_annotation_value_refuses() -> None:
    with pytest.raises(ValueError):
        classify("mit", [AnnotationRef("maybe", "a4")])
