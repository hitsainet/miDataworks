"""DuckDB is confined to the data volume and takes structured filters only (task 10.1)."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.core.errors import AppError
from src.services.duck import connect, quote_ident, where_clause


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
