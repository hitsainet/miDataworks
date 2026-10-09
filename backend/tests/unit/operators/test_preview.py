"""Sampling, previews and statistics without the API (FR-003.19, FR-003.20; FTASKS 9.1-9.5)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.core.config import get_settings
from src.operators import preview
from src.operators.errors import OperatorError
from src.operators.native.fixtures import DropShort, EndpointProbe
from tests.support import operator_fixtures as fx


def _files(data_dir: Path, texts: list[str] | None = None, parts: int = 2) -> list[Path]:
    directory = fx.write_parts(data_dir / "v", fx.table(texts), parts=parts)
    return sorted(directory.glob("part-*.parquet"))


def _request(data_dir: Path, name: str, params: dict[str, Any], **over: Any) -> dict[str, Any]:
    files = over.pop("files", None) or _files(data_dir)
    request = {
        "operator": name,
        "version": "1",
        "params": params,
        "input": {"version_id": "v"},
        "sample_size": None,
        "seed": 3,
        "mode": "preview",
        "resolved_input": {
            "files": [str(p.relative_to(data_dir)) for p in files],
            "column_roles": dict(fx.ROLES),
            "rowkey_scheme": "dw.rowkey/v1",
        },
    }
    request.update(over)
    return request


def test_sampling_is_reproducible_and_capped(data_dir: Path) -> None:
    files = _files(data_dir, [f"row number {i}" for i in range(200)], parts=3)
    a = preview.select_sample(files, 25, seed=7)
    b = preview.select_sample(files, 25, seed=7)
    c = preview.select_sample(files, 25, seed=8)
    keys = lambda t: t.column("_dw_row_key").to_pylist()  # noqa: E731
    assert a.num_rows == 25 and keys(a) == keys(b)
    assert keys(a) != keys(c)
    assert preview.select_sample(files, 1000, seed=7).num_rows == 200


def test_sample_size_defaults_and_caps_by_whether_a_model_is_called() -> None:
    settings = get_settings()
    assert (
        preview.sample_size_for(DropShort.manifest, None)
        == settings.operator_preview_sample_default
    )
    assert preview.sample_size_for(EndpointProbe.manifest, None) == 5
    with pytest.raises(OperatorError):
        preview.sample_size_for(EndpointProbe.manifest, 51)
    with pytest.raises(OperatorError):
        preview.sample_size_for(DropShort.manifest, 2001)


def test_preview_shapes_drops_with_reason_and_statistic(data_dir: Path) -> None:
    result = preview.run_preview(
        fx.registry(), _request(data_dir, "fx_drop_short", {"min_len": 10})
    )
    assert result["counts"] == {
        "in": 8,
        "kept": 5,
        "changed": 0,
        "dropped": 3,
        "added": 0,
        "split_assigned": 0,
    }
    drops = result["examples"]["dropped"]
    assert {d["excerpt"] for d in drops} == {"short", "   ", "x"}
    assert all(d["statistic_name"] == "text_length" and d["threshold"] for d in drops)
    assert len(result["examples"]["kept"]) == 5


def test_dataset_scope_preview_says_within_sample_only(data_dir: Path) -> None:
    result = preview.run_preview(fx.registry(), _request(data_dir, "fx_dedup_exact", {}))
    assert result["note"] == preview.NOTE_WITHIN_SAMPLE
    assert result["counts"]["dropped"] == 1
    assert result["examples"]["dropped"][0]["kept_instead"]


def test_empty_sample_says_no_rows_to_preview(data_dir: Path) -> None:
    files = _files(data_dir, [], parts=1)
    result = preview.run_preview(
        fx.registry(), _request(data_dir, "fx_drop_short", {"min_len": 1}, files=files)
    )
    assert result["empty"] is True and result["message"] == "No rows to preview."


def test_preview_still_enforces_conservation(data_dir: Path) -> None:
    with pytest.raises(OperatorError) as exc:
        preview.run_preview(fx.registry(), _request(data_dir, "fx_lose_row", {}))
    assert exc.value.code == "conservation_violated"


def test_statistics_constant_flag(data_dir: Path) -> None:
    files = _files(data_dir, ["abcde", "fghij", "klmno"], parts=1)
    result = preview.run_statistics(
        fx.registry(),
        _request(data_dir, "fx_drop_short", {"min_len": 3}, files=files, mode="statistics"),
    )
    threshold = result["thresholds"][0]
    assert threshold["constant"] is True and {v["value"] for v in threshold["values"]} == {5.0}
    assert threshold["current"] == 3 and threshold["drop_when"] == "below"


def test_request_hash_ignores_resolved_fields_and_changes_with_params() -> None:
    base = {"operator": "a", "version": "1", "params": {"x": 1}, "input": {}, "seed": 0}
    assert preview.request_hash(base) == preview.request_hash({**base, "resolved_input": {"z": 1}})
    assert preview.request_hash(base) != preview.request_hash({**base, "params": {"x": 2}})


def test_run_request_stores_failures_too(data_dir: Path) -> None:
    request = _request(data_dir, "fx_lose_row", {}, preview_id="unit-" + "a" * 20)
    assert preview.run_request(request)["status"] == "failed"
    stored = preview.fetch(request["preview_id"])
    assert stored is not None and stored["error"]["code"] == "conservation_violated"
