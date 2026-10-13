"""The accounting check fails on each direction of mismatch (tasks 8.3, 8.4; FPRD 002 §12.1).

Built from hand-written Parquet, so the expectation is stated here and not derived from an
operator: input pairs, output pairs and events are written out literally.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.services.step_contract import EVENT_SCHEMA
from src.services.step_ingest import StepFailed, check_accounting

K = [f"{i:064x}" for i in range(6)]


def _rows(pairs: list[tuple[str, int]]) -> pa.Table:
    return pa.table(
        {
            "_dw_row_key": [k for k, _ in pairs],
            "_dw_occurrence": pa.array([o for _, o in pairs], pa.int32()),
        }
    )


def _event(kind: str, key: str, occ: int, **extra: Any) -> dict[str, Any]:
    event = dict.fromkeys(EVENT_SCHEMA.names)
    event.update(kind=kind, row_key=key, occurrence=occ, reason_code="r", reason="because")
    if kind in {"dropped", "changed"}:
        event.update(statistic_name="len", statistic_value=1.0)
    event.update(extra)
    return event


def _meta(**counts: int) -> dict[str, Any]:
    base = {
        "rows_in": 0,
        "rows_kept": 0,
        "rows_changed": 0,
        "rows_dropped": 0,
        "rows_added": 0,
        "rows_split_assigned": 0,
    }
    base.update({f"rows_{k}": v for k, v in counts.items()})
    return base


def run(
    tmp: Path,
    inp: list[tuple[str, int]],
    out: list[tuple[str, int]],
    events: list[dict[str, Any]],
    meta: dict[str, Any],
) -> Any:
    tmp.mkdir(parents=True, exist_ok=True)
    pq.write_table(_rows(inp), tmp / "in.parquet")
    pq.write_table(_rows(out), tmp / "out.parquet")
    pq.write_table(pa.Table.from_pylist(events, schema=EVENT_SCHEMA), tmp / "events.parquet")
    return check_accounting(
        [tmp / "in.parquet"], [tmp / "out.parquet"], tmp / "events.parquet", meta, "Step 1 (x 1)"
    )


@pytest.fixture
def where(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    return tmp_path / "step"


def test_a_closed_step_with_duplicates_passes(where: Path) -> None:
    inp = [(K[0], 0), (K[0], 1), (K[1], 0), (K[2], 0)]
    out = [(K[0], 1), (K[1], 0), (K[3], 0), (K[4], 0)]
    events = [
        _event("dropped", K[0], 0),
        _event("changed", K[2], 0, new_row_key=K[3], new_occurrence=0),
        _event("added", K[4], 0),
    ]
    result = run(
        where,
        inp,
        out,
        events,
        _meta(**{"in": 4, "kept": 2, "changed": 1, "dropped": 1, "added": 1}),
    )
    assert (result.rows_in, result.rows_kept, result.rows_dropped) == (4, 2, 1)
    assert result.reason_counts[0]["count"] == 1


def test_dropping_one_copy_of_a_duplicate_key_is_counted_by_pair(where: Path) -> None:
    """A key-only check would see K0 still present and miss that occurrence 0 left."""
    inp = [(K[0], 0), (K[0], 1)]
    with pytest.raises(StepFailed) as info:
        run(where, inp, [(K[0], 1)], [], _meta(**{"in": 2, "kept": 2}))
    assert info.value.details["direction"] == "input_row_lost"


@pytest.mark.parametrize(
    ("inp", "out", "events", "direction"),
    [
        ([(K[0], 0), (K[1], 0)], [(K[0], 0)], [], "input_row_lost"),
        ([(K[0], 0)], [(K[0], 0), (K[5], 0)], [], "output_row_unaccounted"),
        ([(K[0], 0)], [(K[0], 0)], [_event("dropped", K[5], 0)], "event_for_missing_input"),
        (
            [(K[0], 0)],
            [],
            [_event("changed", K[0], 0, new_row_key=K[1], new_occurrence=0)],
            "event_target_missing",
        ),
        ([(K[0], 0)], [(K[0], 0), (K[0], 0)], [], "output_duplicate_pair"),
    ],
    ids=["lost", "unaccounted", "phantom-drop", "changed-missing", "duplicate-pair"],
)
def test_each_mismatch_direction_fails(
    where: Path,
    inp: list[tuple[str, int]],
    out: list[tuple[str, int]],
    events: list[dict[str, Any]],
    direction: str,
) -> None:
    with pytest.raises(StepFailed) as info:
        run(where, inp, out, events, _meta(**{"in": len(inp), "kept": len(inp)}))
    assert info.value.code == "accounting_mismatch"
    assert info.value.details["direction"] == direction
    assert info.value.details["sample"]


def test_counts_that_disagree_with_the_rows_fail(where: Path) -> None:
    with pytest.raises(StepFailed) as info:
        run(where, [(K[0], 0)], [(K[0], 0)], [], _meta(**{"in": 1, "kept": 0, "dropped": 1}))
    assert info.value.details["direction"] == "counts_disagree"


@pytest.mark.parametrize(
    "fields",
    [{"reason_code": ""}, {"reason": ""}, {"statistic_name": None}, {"statistic_value": None}],
    ids=["no-code", "no-reason", "no-statistic-name", "no-statistic-value"],
)
def test_an_unreasoned_drop_fails(where: Path, fields: dict[str, Any]) -> None:
    with pytest.raises(StepFailed) as info:
        run(
            where,
            [(K[0], 0)],
            [],
            [_event("dropped", K[0], 0, **fields)],
            _meta(**{"in": 1, "dropped": 1}),
        )
    assert info.value.code == "unreasoned_event"


def test_a_textual_statistic_is_a_statistic(where: Path) -> None:
    event = _event(
        "dropped",
        K[0],
        0,
        statistic_value=None,
        statistic_text="profanity",
        threshold=json.dumps({"value": 1}),
    )
    run(where, [(K[0], 0)], [], [event], _meta(**{"in": 1, "dropped": 1}))


def test_incomplete_meta_is_refused(where: Path) -> None:
    from src.services.step_contract import StepMetaIncomplete, read_meta

    where.mkdir(parents=True)
    (where / "meta.json").write_text(json.dumps({"rows_in": 1, "output_column_roles": {}}))
    with pytest.raises(StepMetaIncomplete):
        read_meta(where)
