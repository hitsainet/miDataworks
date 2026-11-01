"""Run one catalogue operator on the contract fixture through the RUNNER's own ``apply``."""

from __future__ import annotations

import json
from typing import Any

import pyarrow.parquet as pq

import dj_paths


def kind_of(op_name: str) -> str:
    catalogue = json.loads(
        (dj_paths.BACKEND / "src" / "operators" / "datajuicer" / "catalogue.json").read_text()
    )
    for item in catalogue["operators"]:
        if item["op_name"] == op_name:
            return str(item["manifest"]["kind"])
    raise KeyError(op_name)


def outcome(op_name: str, params: dict[str, Any]) -> dict[str, Any]:
    from src.operators.datajuicer import runner

    table = pq.read_table(dj_paths.FIXTURE)
    output, decisions = runner.apply(table, op_name, kind_of(op_name), params, 1)
    kept = sorted(
        [k, o]
        for k, o in zip(
            output.column("_dw_row_key").to_pylist(),
            output.column("_dw_occurrence").to_pylist(),
            strict=True,
        )
    )
    dropped = sorted(
        (
            {
                "row_key": d["row_key"],
                "occurrence": d["occurrence"],
                "stats": json.loads(d["stats_json"]) if d["stats_json"] else None,
                "kept_key": d["kept_key"],
            }
            for d in decisions.to_pylist()
            if not d["keep"]
        ),
        key=lambda d: (d["row_key"], d["occurrence"]),
    )
    stats = sorted(
        json.dumps(json.loads(d["stats_json"]), sort_keys=True)
        for d in decisions.to_pylist()
        if d["stats_json"]
    )
    return {"params": params, "kept": kept, "dropped": dropped, "distinct_stats": len(set(stats))}
