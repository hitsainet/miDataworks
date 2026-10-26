"""The content hash, pinned by the ``hash`` vectors (FR-018.8; FTASKS 3.2).

Each vector gives a document and its ``code_sha256``; miDataworks' hash must equal it. The vectors
include a non-ASCII key and two keys whose code-point and UTF-16 orders differ, so the negative
controls (UTF-16 sort, ``ensure_ascii=True``, hashing the cases too) each turn a vector red.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from src.runner_contract import cases as cases_mod
from src.runner_contract.hashing import canonical_document_bytes, code_sha256

CASES = cases_mod.load_cases()
HASH_VECTORS = CASES["hash"]
DOCUMENTS = CASES["documents"]


def test_there_is_a_nonascii_key_vector_and_a_utf16_order_vector() -> None:
    ids = {v["id"] for v in HASH_VECTORS}
    assert {"hash.nonascii_key", "hash.utf16_order"} <= ids
    order_doc = DOCUMENTS["hash_utf16_order"]["params_schema"]["properties"]
    keys = list(order_doc)
    assert sorted(keys) != sorted(keys, key=lambda k: k.encode("utf-16-be"))


@pytest.mark.parametrize("vector", HASH_VECTORS, ids=[v["id"] for v in HASH_VECTORS])
def test_our_hash_equals_the_vector(vector: dict) -> None:
    document = DOCUMENTS[vector["document"]]
    assert canonical_document_bytes(document).decode("utf-8") == vector["canonical_utf8"]
    assert code_sha256(document) == vector["sha256"]


@pytest.mark.parametrize("vector", HASH_VECTORS, ids=[v["id"] for v in HASH_VECTORS])
def test_a_utf16_sort_breaks_at_least_one_vector(vector: dict) -> None:
    document = DOCUMENTS[vector["document"]]

    def utf16_canonical(value: object) -> bytes:
        return json.dumps(value, sort_keys=False, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-16-be"
        )

    # Sort every mapping by UTF-16 order instead of code point.
    def resort(value: object) -> object:
        if isinstance(value, dict):
            return {k: resort(value[k]) for k in sorted(value, key=lambda k: k.encode("utf-16-be"))}
        if isinstance(value, list):
            return [resort(v) for v in value]
        return value

    wrong = json.dumps(resort(document), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    # For the order vector the bytes (and so the hash) must differ from the code-point hash.
    if vector["id"] == "hash.utf16_order":
        assert hashlib.sha256(wrong).hexdigest() != vector["sha256"]
    assert utf16_canonical  # keep the helper referenced


def test_ensure_ascii_true_breaks_the_nonascii_vector() -> None:
    document = DOCUMENTS["hash_nonascii_key"]
    wrong = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )
    vector = next(v for v in HASH_VECTORS if v["id"] == "hash.nonascii_key")
    assert hashlib.sha256(wrong).hexdigest() != vector["sha256"]


def test_hashing_the_cases_too_would_change_the_hash() -> None:
    document = dict(DOCUMENTS["hash_ascii"])
    with_cases = {
        **document,
        "cases": [{"id": "x", "function": "f", "params": {}, "text": "", "expect": {"count": 0}}],
    }
    assert code_sha256(with_cases) != code_sha256(document)
