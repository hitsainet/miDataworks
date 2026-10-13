"""Data-volume paths and the stage-then-rename writer (Foundation tasks 4.1, 4.2, 4.4, 4.5)."""

from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.core import storage
from src.core.storage import (
    LAYOUT,
    PathOutsideDataDir,
    atomic_write,
    atomic_write_bytes,
    clean_tmp_on_worker_start,
    ensure_layout,
    list_entries,
    resolve_under_data_dir,
    run_dir,
    staged_path,
    staging_dir,
)


class TestPathConfinement:
    def test_every_layout_directory_resolves_under_data_dir(self, data_dir: Path) -> None:
        ensure_layout()
        for name in LAYOUT:
            path = resolve_under_data_dir(name)
            assert path.is_dir() and data_dir.resolve() in path.parents

    @pytest.mark.parametrize(
        "parts",
        [("..", "etc"), ("versions", "..", "..", "x"), ("/etc/passwd",), ("sources", "../../x")],
    )
    def test_traversal_is_refused(self, data_dir: Path, parts: tuple[str, ...]) -> None:
        with pytest.raises(PathOutsideDataDir):
            resolve_under_data_dir(*parts)

    def test_a_symlink_out_of_the_volume_is_refused(self, data_dir: Path, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        (data_dir / "versions").mkdir()
        os.symlink(outside, data_dir / "versions" / "escape")
        with pytest.raises(PathOutsideDataDir):
            resolve_under_data_dir("versions", "escape", "file")

    @pytest.mark.parametrize("identifier", ["..", ".", "a/b", "", "../x", "x" * 200, "-dash"])
    def test_identifiers_must_be_one_safe_segment(self, data_dir: Path, identifier: str) -> None:
        with pytest.raises(PathOutsideDataDir):
            run_dir(identifier)

    def test_named_helpers_land_where_adr_004_says(self, data_dir: Path) -> None:
        root = data_dir.resolve()
        assert storage.source_dir("src_1") == root / "sources" / "src_1"
        assert storage.version_dir("ver_1") == root / "versions" / "ver_1"
        assert storage.run_dir("job_1") == root / "runs" / "job_1"
        assert storage.export_dir("exp_1") == root / "exports" / "exp_1"
        assert storage.publish_dir("pub_1") == root / "publish" / "pub_1"
        assert storage.hf_cache_dir() == root / "cache" / "hf"
        assert storage.embeddings_cache_dir("abc123") == root / "cache" / "embeddings" / "abc123"
        assert storage.tmp_dir() == root / "tmp"


class TestStageThenRename:
    def test_a_reader_never_sees_a_partial_file(self, data_dir: Path) -> None:
        destination = run_dir("job_x") / "chunk-0001.bin"
        payload = os.urandom(4 * 1024 * 1024)
        observed: list[int] = []
        stop = threading.Event()

        def reader() -> None:
            while not stop.is_set():
                if destination.exists():
                    observed.append(len(destination.read_bytes()))

        thread = threading.Thread(target=reader)
        thread.start()

        def slow_write(handle) -> None:  # type: ignore[no-untyped-def]
            for i in range(0, len(payload), 65536):
                handle.write(payload[i : i + 65536])
                handle.flush()
                # the destination must not exist while we are still writing
                assert not destination.exists()

        try:
            atomic_write(destination, slow_write)
        finally:
            # Always stop the reader: control G17 (writer without staging) hung here instead of
            # failing, because the assertion inside slow_write skipped stop.set().
            stop.set()
            thread.join(timeout=10)
        assert destination.read_bytes() == payload
        assert all(size == len(payload) for size in observed)

    def test_a_failed_write_leaves_no_file_and_no_staging_debris(self, data_dir: Path) -> None:
        destination = run_dir("job_y") / "out.bin"

        def boom(handle) -> None:  # type: ignore[no-untyped-def]
            handle.write(b"half")
            raise RuntimeError("disk full")

        with pytest.raises(RuntimeError):
            atomic_write(destination, boom)
        assert not destination.exists()
        assert list(staging_dir().iterdir()) == []

    def test_the_file_is_written_under_staging_first(self, data_dir: Path) -> None:
        destination = run_dir("job_z") / "a.parquet"
        with staged_path(destination) as staged:
            assert staging_dir() in staged.parents
            staged.write_bytes(b"x")
            assert not destination.exists()
        assert destination.read_bytes() == b"x"

    def test_discovery_never_lists_staging(self, data_dir: Path) -> None:
        ensure_layout()
        (staging_dir() / "in-progress.parquet").write_bytes(b"partial")
        atomic_write_bytes(run_dir("job_1") / "done.parquet", b"whole")
        top = [p.name for p in list_entries()]
        assert "staging" not in top
        with pytest.raises(PathOutsideDataDir):
            list_entries("staging")
        assert [p.name for p in list_entries("runs", "job_1")] == ["done.parquet"]


def test_parquet_through_the_writer_reads_back_with_duckdb(data_dir: Path) -> None:
    """Task 4.4: pyarrow writes through the staged writer; DuckDB reads in-process."""
    texts = [f"text {i}" for i in range(2500)]
    table = pa.table({"id": list(range(2500)), "text": texts})
    destination = storage.version_dir("ver_smoke") / "part-0000.parquet"
    with staged_path(destination) as staged:
        pq.write_table(table, staged)
    con = duckdb.connect()
    count, total = con.execute(
        "SELECT count(*), sum(id) FROM read_parquet(?)", [str(destination)]
    ).fetchone()  # type: ignore[misc]
    joined = con.execute(
        "SELECT string_agg(text, '|' ORDER BY id) FROM read_parquet(?)", [str(destination)]
    ).fetchone()[
        0
    ]  # type: ignore[index]
    con.close()
    assert count == 2500 and total == sum(range(2500))
    expected = hashlib.sha256("|".join(texts).encode()).hexdigest()
    assert hashlib.sha256(joined.encode()).hexdigest() == expected


class TestWorkerStartCleanup:
    def test_tmp_is_emptied(self, data_dir: Path) -> None:
        ensure_layout()
        (storage.tmp_dir() / "scratch.txt").write_text("x")
        (storage.tmp_dir() / "nested").mkdir()
        (storage.tmp_dir() / "nested" / "y").write_text("y")
        assert clean_tmp_on_worker_start() == 2
        assert list(storage.tmp_dir().iterdir()) == []

    def test_staging_is_left_alone(self, data_dir: Path) -> None:
        ensure_layout()
        owned = staging_dir() / "resumable-chunk.parquet"
        owned.write_bytes(b"belongs to a resumable job")
        clean_tmp_on_worker_start()
        assert owned.read_bytes() == b"belongs to a resumable job"

    def test_the_worker_ready_hook_runs_the_cleanup(
        self, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from celery.signals import worker_ready

        from src.workers import startup

        ensure_layout()
        (storage.tmp_dir() / "stale").write_text("x")
        worker_ready.send(sender=None)
        assert list(storage.tmp_dir().iterdir()) == []
        assert startup._on_worker_ready in [r[1]() for r in worker_ready.receivers]
