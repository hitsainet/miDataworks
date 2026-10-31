"""A filtered list over 10,000 behaviour keys is answered from the index (017 FR-017.53, FTASKS 11.1).

10,000 recipes and 10,000 detector sets, each with its own key, are written straight to the
database and analysed. The list tool is called through MCP over the real backend
(``tests/support/mcp_live.py``) with one key: it answers that one object, and the statement the
route ACTUALLY ran (captured from the engine, not read from the source) is planned by PostgreSQL
with an index scan on the behaviour-key index. No time is measured.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import event, text

from src.core.database import get_async_engine, get_sync_engine
from tests.support.mcp_live import call, live_mcp

N = 10_000
KEY = "fx-key-04321"

SEED = {
    "dw_recipes": (
        "INSERT INTO dw_recipes (id, name, archived, created_by, created_by_origin, behaviour_key) "
        "SELECT md5('r' || g)::uuid, 'r-' || g, false, 'T', 'operator', "
        "'fx-key-' || lpad(g::text, 5, '0') "
        "FROM generate_series(1, :n) g"
    ),
    "dw_detector_sets": (
        "INSERT INTO dw_detector_sets (id, name, description, positive_meaning, archived, "
        "created_by, created_by_origin, behaviour_key) "
        "SELECT 'dts_' || md5('s' || g), 's-' || g, '', '', false, 'T', 'operator', "
        "'fx-key-' || lpad(g::text, 5, '0') FROM generate_series(1, :n) g"
    ),
}
TOOLS = {"dw_recipes": "dataworks_list_recipes", "dw_detector_sets": "dataworks_list_detector_sets"}


@contextmanager
def captured() -> Iterator[list[tuple[str, Any]]]:
    """Every statement the backend sends while the block runs, with its parameters."""
    statements: list[tuple[str, Any]] = []

    def record(conn: Any, cursor: Any, statement: str, parameters: Any, *_: Any) -> None:
        statements.append((statement, parameters))

    engines = [get_async_engine().sync_engine, get_sync_engine()]
    for engine in engines:
        event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        for engine in engines:
            event.remove(engine, "before_cursor_execute", record)


def plan(statement: str, parameters: Any) -> str:
    """PostgreSQL's plan for the statement the route ran, with its own parameters."""
    raw = get_sync_engine().raw_connection()
    try:
        cursor = raw.cursor()
        cursor.execute("SET enable_seqscan = on")
        sql = statement
        args: Any = parameters
        if isinstance(parameters, tuple | list) and "$1" in statement:
            # asyncpg's numbered parameters: inline them as literals for EXPLAIN
            for i in range(len(parameters), 0, -1):
                value = parameters[i - 1]
                literal = (
                    "'" + str(value).replace("'", "''") + "'"
                    if isinstance(value, str)
                    else ("NULL" if value is None else str(value))
                )
                sql = sql.replace(f"${i}::VARCHAR", literal).replace(f"${i}", literal)
            args = None
        cursor.execute("EXPLAIN " + sql, args)
        return "\n".join(row[0] for row in cursor.fetchall())
    finally:
        raw.close()


@pytest.mark.parametrize("table", sorted(SEED))
async def test_a_filtered_list_is_answered_from_the_index(
    client: Any, operator_name: str, table: str
) -> None:
    with get_sync_engine().begin() as conn:
        conn.execute(text(SEED[table]), {"n": N})
        conn.execute(text(f"ANALYZE {table}"))
    mcp, mcp_client = live_mcp()
    try:
        with captured() as statements:
            answer = await call(mcp, TOOLS[table], {"behaviour_key": KEY})
    finally:
        await mcp_client.close()
    assert answer["total"] == 1 and len(answer["items"]) == 1, answer
    keyed = [
        (s, p)
        for s, p in statements
        if f"FROM {table}" in s and "behaviour_key =" in s and "count(" not in s.lower()
    ]
    assert keyed, [s for s, _ in statements]
    statement, parameters = keyed[0]
    assert KEY in (list(parameters.values()) if isinstance(parameters, dict) else list(parameters))
    explained = plan(statement, parameters)
    assert f"ix_{table}_behaviour_key" in explained, explained
    assert "Seq Scan on " + table not in explained, explained
