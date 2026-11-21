"""DuckDB is confined to the data volume and takes structured filters only (task 10.1)."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.core.errors import AppError
from src.services.duck import arrow_reader, arrow_table, connect, quote_ident, where_clause


@pytest.fixture
def volume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from src.core.config import get_settings

    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(get_settings(), "data_dir", root)
    return root


def test_files_inside_the_volume_are_readable_and_outside_are_not(
    volume: Path, tmp_path: Path
) -> None:
    inside, outside = volume / "in.parquet", tmp_path / "out.parquet"
    pq.write_table(pa.table({"a": [1, 2]}), inside)
    pq.write_table(pa.table({"a": [3]}), outside)
    con = connect()
    assert con.execute("SELECT count(*) FROM read_parquet(?)", [str(inside)]).fetchone() == (2,)
    with pytest.raises(duckdb.Error):
        con.execute("SELECT count(*) FROM read_parquet(?)", [str(outside)])
    with pytest.raises(duckdb.Error):
        con.execute("SET enable_external_access=true")


def test_unknown_columns_and_operators_are_refused() -> None:
    with pytest.raises(AppError) as info:
        quote_ident("text; DROP", ["text"])
    assert info.value.code == "unknown_column"
    with pytest.raises(AppError) as info:
        where_clause([{"column": "text", "op": "raw", "value": "1=1"}], ["text"])
    assert info.value.code == "filter_invalid"


def test_values_are_bound_never_inlined() -> None:
    clause, params = where_clause(
        [{"column": "text", "op": "eq", "value": "x' OR '1'='1"}], ["text"]
    )
    assert clause == ' WHERE "text" = ?' and params == ["x' OR '1'='1"]


# --- Arrow fetches (DuckDB 2.0, 2026-10-22) -----------------------------------------------------
# DuckDB 2.0 removed ``fetch_record_batch`` (signed split downloads answered 500 on it), types the
# connection's fetches as ``X | None``, and copies the rows when Arrow is read from an ``execute``
# result. Every Arrow fetch in src goes through these two helpers, which read through a relation;
# these tests read real Parquet through the installed DuckDB.


def test_arrow_table_returns_the_queried_rows(volume: Path) -> None:
    path = volume / "t.parquet"
    pq.write_table(pa.table({"text": ["a", "bb", "ccc"], "n": [1, 2, 3]}), path)
    table = arrow_table(connect(), "SELECT text, n FROM read_parquet(?) ORDER BY n", [str(path)])
    assert isinstance(table, pa.Table)
    assert table.to_pylist() == [
        {"text": "a", "n": 1},
        {"text": "bb", "n": 2},
        {"text": "ccc", "n": 3},
    ]


def test_arrow_reader_streams_every_row_in_batches_of_the_asked_size(volume: Path) -> None:
    path = volume / "r.parquet"
    pq.write_table(pa.table({"n": list(range(10))}), path)
    reader = arrow_reader(connect(), "SELECT n FROM read_parquet(?) ORDER BY n", [str(path)], 4)
    assert isinstance(reader, pa.RecordBatchReader)
    batches = list(reader)
    assert [b.num_rows for b in batches] == [4, 4, 2]
    assert [r["n"] for b in batches for r in b.to_pylist()] == list(range(10))


def test_the_helpers_stay_inside_the_volume(volume: Path, tmp_path: Path) -> None:
    # They run on the caller's confined connection, so the volume restriction still holds.
    outside = tmp_path / "out.parquet"
    pq.write_table(pa.table({"a": [1]}), outside)
    with pytest.raises(duckdb.Error):
        arrow_table(connect(), "SELECT * FROM read_parquet(?)", [str(outside)])
    with pytest.raises(duckdb.Error):
        list(arrow_reader(connect(), "SELECT * FROM read_parquet(?)", [str(outside)]))


def test_the_helpers_stream_rather_than_copy(volume: Path) -> None:
    # DuckDB 2.0 warns "Arrow output on a DB-API result makes an extra copy of the rows" when Arrow
    # is read from an execute() result; that path peaked at 434 MB on 3M rows against 195 MB for a
    # relation (0xcc/reviews/deps_sync_2026-10-21.md). The warning is the tripwire: an error here.
    import warnings

    path = volume / "w.parquet"
    pq.write_table(pa.table({"n": list(range(1000))}), path)
    con = connect()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert arrow_table(con, "SELECT n FROM read_parquet(?)", [str(path)]).num_rows == 1000
        assert (
            sum(b.num_rows for b in arrow_reader(con, "SELECT n FROM read_parquet(?)", [str(path)]))
            == 1000
        )


def test_a_half_read_execute_result_does_not_block_the_fetch(volume: Path) -> None:
    # The pattern every caller uses: one row of a count with fetchone(), then the Arrow fetch on
    # the same connection. DuckDB 2.0 refuses the relation unless the first result is finished.
    path = volume / "c.parquet"
    pq.write_table(pa.table({"n": list(range(5))}), path)
    con = connect()
    total = con.execute("SELECT count(*) FROM read_parquet(?)", [str(path)]).fetchone()
    assert total == (5,)
    assert arrow_table(con, "SELECT n FROM read_parquet(?)", [str(path)]).num_rows == 5
    con.execute("SELECT n FROM read_parquet(?)", [str(path)]).fetchone()
    assert (
        sum(b.num_rows for b in arrow_reader(con, "SELECT n FROM read_parquet(?)", [str(path)]))
        == 5
    )


def test_a_missing_result_fails_loudly_instead_of_returning_none() -> None:
    class NoRelation:
        def to_arrow_table(self) -> None:
            return None

        def to_arrow_reader(self, batch_rows: int) -> None:
            return None

    class NoResult:
        def complete(self) -> None:
            return None

        def sql(self, query: str, params: object = None) -> NoRelation:
            return NoRelation()

    with pytest.raises(RuntimeError, match="no result"):
        arrow_table(NoResult(), "SELECT 1")  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="no result"):
        arrow_reader(NoResult(), "SELECT 1", None, 4)  # type: ignore[arg-type]
