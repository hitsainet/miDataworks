"""``manifest_builder`` and the two keys added within v1 (021 FTID I2; FTASKS 2.1, 2.3b, 9.1, 9.3;
R1A-D1, R2B-24).

The model requires ``sensitivity`` and each rows split's ``wsp``, and every writer passes them.
``with_publication`` still reads back stored manifests written before 021 (or by push 1) that lack
them: it must accept them, return them as given, and check the document it returns against the
committed file, not only its filled copy.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.services.publishing import manifest_builder as mb
from tests.support.manifest_fixtures import published, rows_manifest

PUBLISHED = published(rows_manifest())["publication"]


def stripped() -> dict[str, Any]:
    doc = rows_manifest()
    del doc["sensitivity"]
    for split in doc["content"]["splits"]:
        del split["wsp"]
    return doc


def test_build_document_requires_sensitivity() -> None:
    """FR-021.21: a required keyword, so no manifest site can forget it (M-26)."""
    import inspect

    params = inspect.signature(mb.build_document).parameters
    for name in ("sensitivity", "sensitivity_basis"):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert params[name].default is inspect.Parameter.empty


def test_build_document_emits_both_keys_and_the_basis() -> None:
    doc = rows_manifest()
    data = mb.build_document(
        version=doc["version"],
        target=doc["target"],
        sources=doc["sources"],
        content=doc["content"],
        caveats=doc["caveats"],
        publication=None,
        sensitivity="sensitive",
        sensitivity_basis={"floor": "sensitive", "declared": "sensitive", "raised_by": []},
    )
    assert data["sensitivity"] == "sensitive"
    assert [s["wsp"] for s in data["content"]["splits"]] == [None, None]
    assert data["extensions"]["sensitivity_basis"]["floor"] == "sensitive"


def test_with_publication_accepts_a_stored_document_without_the_keys() -> None:
    """R1A-D1: a stored pre-021 (or push-1) manifest, read back after the Hub upload."""
    out = mb.with_publication(stripped(), PUBLISHED)
    assert out["publication"] == PUBLISHED
    assert "sensitivity" not in out
    assert all("wsp" not in s for s in out["content"]["splits"])


def test_with_publication_accepts_a_stored_document_with_the_keys() -> None:
    doc = rows_manifest()
    doc["sensitivity"] = "sensitive"
    out = mb.with_publication(doc, PUBLISHED)
    assert out["sensitivity"] == "sensitive"
    assert [s["wsp"] for s in out["content"]["splits"]] == [None, None]


def test_with_publication_validates_the_document_it_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2B-24 (M-48): the file checks the bytes the publish job writes, not only the filled
    copy. The fill only adds nulls the file allows, so a document the file refuses fails both;
    what can silently go is the check of the RETURNED document, so the call is pinned by its
    payload and count."""
    seen: list[dict[str, Any]] = []
    real = mb.validate_against_file

    def spy(document: Any) -> None:
        seen.append(dict(document))
        real(document)

    monkeypatch.setattr(mb, "validate_against_file", spy)
    out = mb.with_publication(stripped(), PUBLISHED)
    assert len(seen) == 2
    assert out in seen  # the returned document, without the two keys
    assert any("sensitivity" in s for s in seen)  # and the filled copy


def test_with_publication_refuses_what_the_file_refuses() -> None:
    doc = stripped()
    doc["content"]["splits"][0]["held_out"] = "yes"
    with pytest.raises(Exception) as caught:
        mb.with_publication(doc, PUBLISHED)
    assert "held_out" in str(caught.value)
