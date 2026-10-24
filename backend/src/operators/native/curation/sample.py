"""``sample``: keep a seeded random sample of rows, by count or by share (novice walk 2026-10-19).

The guided flow had no way to make "25,000 rows of a 200,000-row source" (finding 4 of the novice
walk; 005 FPRD success criterion 12). This selector keeps a uniform random sample without
replacement over the WHOLE input:

- Rows are put in (row key, occurrence) order first (``key_order``), so the draw never depends on
  file or batch order. The sample is ``rng.permutation(n)[:k]`` with ``rng`` seeded from the
  ``seed`` parameter when given, else from the build's step seed (recorded on the version). Same
  input, same parameters, same seed: the same rows, every time.
- ``mode: count`` keeps ``count`` rows (all of them when the input has fewer, said in the report);
  ``mode: fraction`` keeps ``round(fraction * n)`` rows (half up), at least one when the input has
  any.
- Every row not kept gets a ``dropped`` event, reason ``not_sampled``, naming its rank in the draw
  (``sample_rank``) against the number kept: a row is kept when its rank is below that number. The
  drop log therefore says, for every row, why it is absent and which draw left it out.
- Splits are untouched: no row moves between splits; the report counts rows per split before and
  after, so a sampled held-out split is visible.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pyarrow as pa

from ...context import RunContext
from ...protocol import OperatorResult
from .common import key_order, manifest, read_all_or_empty

MODES = ("count", "fraction")
SEED_MAX = 2**32 - 1

PARAMS: dict[str, Any] = {
    "mode": {
        "type": "string",
        "enum": list(MODES),
        "default": "count",
        "title": "Sample by",
        "x-hint": "count keeps a fixed number of rows; fraction keeps a share of them.",
    },
    "count": {
        "type": "integer",
        "minimum": 1,
        "title": "Rows to keep",
        "x-unit": "rows",
        "x-hint": "Used when sampling by count. An input with fewer rows is kept whole.",
    },
    "fraction": {
        "type": "number",
        "exclusiveMinimum": 0,
        "maximum": 1,
        "title": "Share of rows to keep",
        "x-unit": "share, 0 to 1",
        "x-hint": "Used when sampling by fraction: 0.125 keeps one row in eight.",
    },
    "seed": {
        "type": "integer",
        "minimum": 0,
        "maximum": SEED_MAX,
        "title": "Seed",
        "x-advanced": True,
        "x-hint": "Fixes which rows are drawn. Empty: the build's step seed, which the version "
        "records, so a rebuild with the same seed draws the same rows.",
    },
}


def check_params(params: Mapping[str, Any]) -> list[str]:
    """The rule the schema subset cannot state: the chosen mode's size is given, the other is not.

    Called by the registry beside the schema check (``validate_params_detailed``), so a recipe is
    refused when it is saved, not when its build reaches this step.
    """
    mode = params.get("mode") or "count"
    other = "fraction" if mode == "count" else "count"
    problems = []
    if params.get(mode) is None:
        size = "a number of rows" if mode == "count" else "a share between 0 and 1"
        problems.append(f"/{mode}: sampling by {mode} needs {size}.")
    if params.get(other) is not None:
        problems.append(
            f"/{other}: remove {other} when sampling by {mode}; only one size can apply."
        )
    return problems


def kept_rows(n: int, params: Mapping[str, Any]) -> int:
    """How many of ``n`` rows the parameters keep."""
    if n == 0:
        return 0
    if (params.get("mode") or "count") == "count":
        return min(int(params["count"]), n)
    k = int(np.floor(float(params["fraction"]) * n + 0.5))
    return max(1, min(k, n))


class Sample:
    manifest = manifest(
        "sample",
        "selector",
        "Keeps a seeded random sample of the rows, by count or by share; every row left out is "
        "logged with its rank in the draw. The same seed draws the same rows.",
        scope="dataset",
        params=PARAMS,
    )

    def check_params(self, params: Mapping[str, Any]) -> list[str]:
        return check_params(params)

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        table = key_order(read_all_or_empty(ctx, batch))
        n = table.num_rows
        seed = int(params["seed"]) if params.get("seed") is not None else int(ctx.step_seed)
        k = kept_rows(n, params)
        ranks = np.empty(n, dtype=np.int64)
        ranks[np.random.default_rng(seed).permutation(n)] = np.arange(n, dtype=np.int64)
        keep = ranks < k
        keys = table.column("_dw_row_key").to_pylist() if n else []
        occ = table.column("_dw_occurrence").to_pylist() if n else []
        reason = f"not among the {k:,} of {n:,} rows drawn (seed {seed})"
        events = [
            ctx.drop(
                (keys[i], occ[i]),
                "not_sampled",
                reason,
                "sample_rank",
                float(ranks[i]),
                k,
                "kept_below",
            )
            for i in np.flatnonzero(~keep).tolist()
        ]
        splits = (
            [s if s is not None else "" for s in table.column("_dw_split").to_pylist()]
            if "_dw_split" in table.schema.names
            else [""] * n
        )
        per_split: dict[str, dict[str, int]] = {}
        for split, kept in zip(splits, keep.tolist(), strict=True):
            row = per_split.setdefault(split, {"before": 0, "after": 0})
            row["before"] += 1
            row["after"] += int(kept)
        mode = params.get("mode") or "count"
        report = {
            "mode": mode,
            "requested": params.get(mode),
            "seed": seed,
            "seed_source": "parameter" if params.get("seed") is not None else "step_seed",
            "rows_in": n,
            "rows_kept": k,
            "rows_dropped": n - k,
            "kept_whole": mode == "count" and n > 0 and int(params["count"]) >= n,
            "splits": per_split,
        }
        return OperatorResult(output=table.filter(pa.array(keep)), events=events, report=report)


OPERATORS = (Sample,)
