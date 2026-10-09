"""Upload sniffing and validation (001 FTASKS 8.1–8.4; FR-001.21–001.24).

A CSV named ``.parquet`` → ``upload_format_mismatch``; a JSONL file with a bad line 7 →
``upload_malformed {line: 7}``; a CSV field-count mismatch reports its line; a ``_dw_x`` column →
``reserved_column``; each format converts to Parquet whose rows come from metadata.
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.core.errors import AppError
from src.schemas.sources import CsvOptions, UploadManifest
from src.services.sources import upload_service
from src.services.sources.hashing import hash_file
from src.services.sources.upload_service import Received, sniff_format, validate_and_convert


def received(path: Path, name: str) -> Received:
    return Received(path, path.stat().st_size, hash_file(path), name)


def write(tmp_path: Path, name: str, content: bytes) -> Path:
    path = tmp_path / "staged.bin"
    path.write_bytes(content)
    return path


def convert(tmp_path: Path, name: str, content: bytes, csv: dict | None = None) -> dict:
    path = write(tmp_path, name, content)
    return validate_and_convert(received(path, name), "train", csv, tmp_path / "out.parquet")


def parquet_bytes(tmp_path: Path, rows: list[dict]) -> bytes:
    path = tmp_path / "src.parquet"
    pq.write_table(pa.Table.from_pylist(rows), path)
    return path.read_bytes()


def test_sniffing_reads_content_not_names(tmp_path: Path) -> None:
    assert sniff_format(write(tmp_path, "x", parquet_bytes(tmp_path, [{"a": 1}]))) == "parquet"
    assert sniff_format(write(tmp_path, "x", b'\n  {"a": 1}\n')) == "jsonl"
    assert sniff_format(write(tmp_path, "x", b"a,b\n1,2\n")) == "csv"


def test_a_csv_named_parquet_is_a_format_mismatch(tmp_path: Path) -> None:
    with pytest.raises(AppError) as info:
        convert(tmp_path, "data.parquet", b"text,label\nhello there,1\n")
    assert info.value.code == "upload_format_mismatch"
    assert info.value.details == {"file": "data.parquet", "sniffed": "csv"}


def test_a_jsonl_file_with_a_bad_line_7_names_line_7(tmp_path: Path) -> None:
    lines = [json.dumps({"text": f"row {i}"}) for i in range(10)]
    lines[6] = '{"text": "broken'
    with pytest.raises(AppError) as info:
        convert(tmp_path, "rows.jsonl", "\n".join(lines).encode())
    assert info.value.code == "upload_malformed" and info.value.details["line"] == 7


def test_a_jsonl_line_that_is_not_an_object_is_malformed(tmp_path: Path) -> None:
    with pytest.raises(AppError) as info:
        convert(tmp_path, "rows.jsonl", b'{"a": 1}\n[1, 2]\n')
    assert info.value.details["line"] == 2


def test_a_csv_field_count_mismatch_reports_its_line(tmp_path: Path) -> None:
    with pytest.raises(AppError) as info:
        convert(tmp_path, "rows.csv", b"text,label\nfine,1\nalso fine,0\ntoo,many,fields\n")
    assert info.value.code == "upload_malformed" and info.value.details["line"] == 4


@pytest.mark.parametrize(
    ("name", "content_kind"), [("x.parquet", "parquet"), ("x.jsonl", "jsonl"), ("x.csv", "csv")]
)
def test_a_reserved_column_is_refused_in_every_format(
    tmp_path: Path, name: str, content_kind: str
) -> None:
    content = {
        "parquet": parquet_bytes(tmp_path, [{"text": "a", "_dw_x": 1}]),
        "jsonl": b'{"text": "a", "_dw_x": 1}\n',
        "csv": b"text,_dw_x\na,1\n",
    }[content_kind]
    with pytest.raises(AppError) as info:
        convert(tmp_path, name, content)
    assert info.value.code == "reserved_column" and info.value.details["column"] == "_dw_x"
    assert not (tmp_path / "out.parquet").exists()


def test_each_format_converts_and_counts_rows_from_metadata(tmp_path: Path) -> None:
    rows = [{"text": f"sentence number {i}", "label": i % 3} for i in range(25_003)]
    out = convert(tmp_path, "a.parquet", parquet_bytes(tmp_path, rows))
    assert out["rows"] == 25_003 and out["sha256"] == hash_file(tmp_path / "out.parquet")
    out = convert(tmp_path, "a.jsonl", "\n".join(json.dumps(r) for r in rows[:7]).encode())
    assert out["rows"] == 7 and out["parse_options"] == {"format": "jsonl"}
    out = convert(tmp_path, "a.csv", b"text,label\nhello,1\nworld,0\n")
    assert out["rows"] == 2 and out["parse_options"]["delimiter"] == ","


def test_csv_options_are_honoured(tmp_path: Path) -> None:
    out = convert(
        tmp_path,
        "a.tsv",
        b"hello\t007\nworld\t008\n",
        {"delimiter": "\t", "header": False, "type_mode": "text"},
    )
    table = pq.read_table(tmp_path / "out.parquet")
    assert out["rows"] == 2 and table.column(1).to_pylist() == ["007", "008"]
    assert all(str(f.type) == "string" for f in table.schema)


def test_the_original_hash_is_of_the_original_bytes(tmp_path: Path) -> None:
    content = b"text,label\nhello,1\n"
    out = convert(tmp_path, "a.csv", content)
    import hashlib

    assert out["original_sha256"] == hashlib.sha256(content).hexdigest()
    assert out["original_bytes"] == len(content)


def test_an_empty_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(AppError):
        convert(tmp_path, "a.jsonl", b"")


class TestManifest:
    def test_csv_defaults_are_filled(self) -> None:
        assert CsvOptions().model_dump() == upload_service.CSV_DEFAULTS

    def test_several_files_each_with_a_split(self) -> None:
        plan = UploadManifest.model_validate(
            {
                "files": [
                    {"name": "a.parquet", "split": "train"},
                    {"name": "b.jsonl", "split": "test"},
                ]
            }
        )
        assert [f.split for f in plan.files] == ["train", "test"] and plan.csv is None

    def test_unknown_fields_are_refused(self) -> None:
        with pytest.raises(ValueError):
            UploadManifest.model_validate({"files": [{"name": "a", "split": "t"}], "x": 1})
