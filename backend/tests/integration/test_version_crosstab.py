"""A label cross-tab over every row (ENH-006 D116 G5), through the live tool over the real backend.

The expected counts come from a Counter over the fixture's own rows, and the fixture's label is
``deployed_truth`` with a decoy ``label`` that disagrees with it: the dry run's worst fault passed
because every fixture's label column was the run's own.
"""

from __future__ import annotations

import collections
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.services import version_crosstab
from tests.support.gap_fixtures import mark_deleted, split_version
from tests.support.mcp_live import call, live_mcp, refusal_of

KINDS = ("pos", "masked", "bad_check", "spelled")


def _rows(n: int, start: int = 0) -> list[dict[str, Any]]:
    return [
        {
            "text": f"row {start + i}",
            "deployed_truth": i % 3 == 0,
            "label": "negative" if i % 3 == 0 else "positive",  # the decoy: never the same answer
            "kind": KINDS[i % 4],
            "form": None if i % 5 == 0 else ("digits" if i % 2 else "words"),
        }
        for i in range(n)
    ]


async def _tab(arguments: dict[str, Any]) -> dict[str, Any]:
    mcp, mcp_client = live_mcp()
    try:
        answer: dict[str, Any] = await call(mcp, "dataworks_get_version_crosstab", arguments)
        return answer
    finally:
        await mcp_client.close()


async def test_counts_per_split_per_combination_match_the_rows(
    client: httpx.AsyncClient, data_dir: Path
) -> None:
    train, test = _rows(120), _rows(47, start=500)
    version = split_version(data_dir, {"train": train, "test": test})
    out = await _tab({"version_id": version, "columns": ["deployed_truth", "kind"]})
    expected = collections.Counter(
        (split, r["deployed_truth"], r["kind"])
        for split, rows in (("train", train), ("test", test))
        for r in rows
    )
    got = {
        (c["split"], c["values"]["deployed_truth"], c["values"]["kind"]): c["rows"]
        for c in out["cells"]
    }
    assert got == dict(expected)
    assert out["truncated"] is False and out["cells_total"] == len(expected)
    assert out["rows_total"] == 167
    assert [(s["name"], s["rows"], s["rows_recorded"]) for s in out["splits"]] == [
        ("train", 120, 120),
        ("test", 47, 47),
    ]
    assert [c["rows"] for c in out["cells"]] == sorted(
        (c["rows"] for c in out["cells"]), reverse=True
    )


async def test_one_split_counts_only_that_split(client: httpx.AsyncClient, data_dir: Path) -> None:
    version = split_version(data_dir, {"train": _rows(120), "test": _rows(47, start=500)})
    out = await _tab({"version_id": version, "columns": ["kind"], "split": "test"})
    assert out["rows_total"] == 47 and out["split"] == "test"
    assert {c["split"] for c in out["cells"]} == {"test"}
    assert [s["name"] for s in out["splits"]] == ["test"]


async def test_nulls_are_a_combination_and_a_column_one_split_lacks_counts_as_null(
    client: httpx.AsyncClient, data_dir: Path
) -> None:
    train = _rows(20)
    test = [{"text": f"other {i}", "kind": "pos"} for i in range(9)]
    version = split_version(data_dir, {"train": train, "test": test})
    out = await _tab({"version_id": version, "columns": ["form", "kind"]})
    assert sum(1 for r in train if r["form"] is None) > 0
    nulls = sum(
        c["rows"] for c in out["cells"] if c["split"] == "train" and c["values"]["form"] is None
    )
    assert nulls == sum(1 for r in train if r["form"] is None)
    lacking = next(s for s in out["splits"] if s["name"] == "test")
    assert lacking["columns_absent"] == ["form"] and lacking["rows"] == 9
    assert {"split": "test", "values": {"form": None, "kind": "pos"}, "rows": 9} in out["cells"]


async def test_past_the_cap_it_says_so_and_still_adds_up(
    client: httpx.AsyncClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(version_crosstab, "MAX_CELLS", 10)
    rows = [{"text": "a long value " + "x" * 300 + str(i), "kind": "pos"} for i in range(25)]
    rows += [{"text": "a long value " + "x" * 300 + "same", "kind": "pos"} for _ in range(5)]
    version = split_version(data_dir, {"train": rows})
    out = await _tab({"version_id": version, "columns": ["text"]})
    assert out["truncated"] is True and out["cap"] == 10
    assert out["cells_total"] == 26 and len(out["cells"]) == 10
    assert out["rows_total"] == 30 and out["splits"][0]["rows"] == 30
    assert out["cells"][0]["rows"] == 5, "the largest first, so a cut keeps what matters"
    assert all(len(c["values"]["text"]) <= version_crosstab.VALUE_CHARS + 3 for c in out["cells"])


@pytest.mark.parametrize(
    ("arguments", "code", "status"),
    [
        ({"columns": ["nope"]}, "unknown_column", 422),
        ({"columns": ["kind"], "split": "nope"}, "split_not_found", 404),
        ({"columns": ["kind", "kind"]}, "columns_invalid", 422),
    ],
)
async def test_refusals_are_named(
    client: httpx.AsyncClient,
    data_dir: Path,
    arguments: dict[str, Any],
    code: str,
    status: int,
) -> None:
    version = split_version(data_dir, {"train": _rows(4)})
    with pytest.raises(ToolError) as exc:
        await _tab({"version_id": version, **arguments})
    assert refusal_of(exc.value)[:2] == (code, status)


async def test_the_tool_takes_at_most_four_columns(
    client: httpx.AsyncClient, data_dir: Path
) -> None:
    version = split_version(data_dir, {"train": _rows(4)})
    with pytest.raises(Exception, match="4"):
        await _tab({"version_id": version, "columns": ["a", "b", "c", "d", "e"]})
    out = await client.get(
        f"/api/v1/versions/{version}/crosstab",
        params={"columns": ["kind", "form", "label", "deployed_truth", "text"]},
    )
    assert out.status_code == 422


async def test_a_deleted_version_is_refused(client: httpx.AsyncClient, data_dir: Path) -> None:
    version = split_version(data_dir, {"train": _rows(4)})
    mark_deleted(version)
    got = await client.get(f"/api/v1/versions/{version}/crosstab", params={"columns": ["kind"]})
    assert got.status_code == 409 and got.json()["error"]["code"] == "version_not_completed"


async def test_it_runs_off_the_event_loop(
    client: httpx.AsyncClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A big file's DuckDB read must not stall every other request."""
    import threading

    real = version_crosstab.crosstab
    threads: list[str] = []

    def spy(*args: Any, **kwargs: Any) -> dict[str, Any]:
        threads.append(threading.current_thread().name)
        return real(*args, **kwargs)

    monkeypatch.setattr(version_crosstab, "crosstab", spy)
    version = split_version(data_dir, {"train": _rows(4)})
    got = await client.get(f"/api/v1/versions/{version}/crosstab", params={"columns": ["kind"]})
    assert got.status_code == 200
    assert len(threads) == 1 and threads[0] != threading.main_thread().name
