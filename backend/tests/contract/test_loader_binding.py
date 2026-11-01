"""The production loader is ``datasets.load_dataset`` (001 FTASKS 7.11, 13.4).

Integration tests inject a fake through ``hf_materialise.Loader``; this proves the default binding
is the real function and that local Parquet and JSONL read through it pass through the SAME writer
the import uses (record batches, fsync, hash from disk, rows from metadata).
"""

from __future__ import annotations

import json
from pathlib import Path

import datasets
import pyarrow as pa
import pyarrow.parquet as pq

from src.services.sources import hf_materialise
from src.services.sources.hashing import hash_file
from src.workers import source_tasks


def test_the_default_loader_is_datasets_load_dataset() -> None:
    assert hf_materialise.default_loader() is datasets.load_dataset


def test_the_worker_uses_the_default_unless_a_test_replaces_it() -> None:
    assert source_tasks.LOADER is None


def _check(written: hf_materialise.WrittenFile, rows: int) -> None:
    assert written.rows == rows
    assert written.sha256 == hash_file(written.path)
    assert pq.ParquetFile(written.path).metadata.num_rows == rows


def test_local_parquet_through_load_dataset_and_the_writer(tmp_path: Path) -> None:
    source = tmp_path / "in.parquet"
    pq.write_table(
        pa.Table.from_pylist([{"text": f"row {i}", "y": i} for i in range(25_000)]), source
    )
    data = datasets.load_dataset(
        "parquet", data_files={"train": str(source)}, cache_dir=str(tmp_path / "cache")
    )
    out = tmp_path / "staging"
    out.mkdir()
    written = hf_materialise.write_split(data["train"], out / "train.parquet")
    _check(written, 25_000)
    assert [c["name"] for c in written.columns] == ["text", "y"]


def test_local_jsonl_through_load_dataset_and_the_writer(tmp_path: Path) -> None:
    source = tmp_path / "in.jsonl"
    source.write_text(
        "\n".join(json.dumps({"text": f"line {i}", "ok": i % 2 == 0}) for i in range(301))
    )
    data = datasets.load_dataset(
        "json", data_files={"train": str(source)}, cache_dir=str(tmp_path / "cache")
    )
    out = tmp_path / "staging"
    out.mkdir()
    _check(hf_materialise.write_split(data["train"], out / "train.parquet"), 301)


def test_materialise_passes_the_pin_and_no_remote_code_to_the_loader(tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def loader(path: str, **kwargs: object) -> object:
        seen.update(kwargs, path=path)
        return datasets.DatasetDict({"train": datasets.Dataset.from_list([{"a": 1}])})

    hf_materialise.materialise(
        loader,
        repo_id="o/r",
        config=None,
        split=None,
        commit="a" * 40,
        cache_dir=tmp_path / "c",
        token="hf_x",
        staging=tmp_path / "s",
    )
    assert seen["revision"] == "a" * 40 and seen["trust_remote_code"] is False
    assert seen["token"] == "hf_x" and seen["cache_dir"] == str(tmp_path / "c")
