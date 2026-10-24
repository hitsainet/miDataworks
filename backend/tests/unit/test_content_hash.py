"""Upload content hash (001 FTASKS 8.3, 8.5; FR-001.25; FTDD 4.5).

Equal for omitted and explicit default options; different for a changed delimiter; independent of
file order; computed through the one canonical-JSON function.
"""

from __future__ import annotations

from src.core.canonical_json import canonical_sha256
from src.services.sources.upload_service import CSV_DEFAULTS, content_hash, parse_options

A = {"split": "train", "sha256": "a" * 64}
B = {"split": "test", "sha256": "b" * 64}


def entry(base: dict, fmt: str, csv: dict | None = None) -> dict:
    return {**base, "parse_options": parse_options(fmt, csv)}


def test_omitted_and_explicit_default_options_hash_the_same() -> None:
    assert content_hash([entry(A, "csv")]) == content_hash([entry(A, "csv", dict(CSV_DEFAULTS))])
    assert content_hash([entry(A, "csv", {"delimiter": ","})]) == content_hash([entry(A, "csv")])


def test_a_changed_delimiter_changes_the_hash() -> None:
    assert content_hash([entry(A, "csv")]) != content_hash([entry(A, "csv", {"delimiter": ";"})])


def test_file_order_does_not_matter() -> None:
    one = [entry(A, "parquet"), entry(B, "jsonl")]
    assert content_hash(one) == content_hash(list(reversed(one)))


def test_the_split_and_the_bytes_are_part_of_identity() -> None:
    base = content_hash([entry(A, "parquet")])
    assert base != content_hash([entry({**A, "split": "validation"}, "parquet")])
    assert base != content_hash([entry({**A, "sha256": "c" * 64}, "parquet")])


def test_extra_entry_fields_are_not_part_of_identity() -> None:
    plain = [entry(A, "parquet")]
    extra = [{**entry(A, "parquet"), "name": "local name.parquet", "staged": "000.bin"}]
    assert content_hash(plain) == content_hash(extra)


def test_it_is_the_canonical_sha256_of_the_sorted_entries() -> None:
    entries = [entry(B, "jsonl"), entry(A, "csv")]
    expected = canonical_sha256(
        sorted(
            (
                {"split": e["split"], "sha256": e["sha256"], "parse_options": e["parse_options"]}
                for e in entries
            ),
            key=lambda e: (e["split"], e["sha256"]),
        )
    )
    assert content_hash(entries) == expected
