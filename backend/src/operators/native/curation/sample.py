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

from ....services.labeling.stratified import stratum_of
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


# --- sample@1.1.0: the stratified mode (017 FR-017.38; FTID §3.8; TD-017.22) ---------------------

STRATIFIED = "stratified"
MODES_V11 = (*MODES, STRATIFIED)
COLUMN = {"type": "string", "pattern": "^[A-Za-z_][A-Za-z0-9_]{0,63}$"}

PARAMS_V11: dict[str, Any] = {
    **PARAMS,
    "mode": {
        **PARAMS["mode"],
        "enum": list(MODES_V11),
        "x-hint": "count keeps a fixed number of rows; fraction keeps a share of them; "
        "stratified keeps up to a number of rows from every stratum, every row a column marks, "
        "and instructed rows at their rate.",
    },
    "strata": {
        "type": "array",
        "items": COLUMN,
        "minItems": 1,
        "uniqueItems": True,
        "title": "Strata (stratified)",
        "x-hint": "The columns whose values make a stratum: genre, generator, kind, label ...",
    },
    "per_stratum": {
        "type": "integer",
        "minimum": 1,
        "title": "Rows per stratum (stratified)",
        "x-unit": "rows",
        "x-hint": "A stratum with fewer rows is kept whole (said in the report).",
    },
    "always_keep_column": {
        **COLUMN,
        "title": "Always keep rows where (stratified)",
        "x-hint": "A boolean column (label_conflict, for example): every row where it is true is "
        "kept and does not count toward its stratum's rows.",
    },
    "instructed_column": {
        **COLUMN,
        "title": "Instructed rows: column (stratified)",
        "x-advanced": True,
        "x-hint": "With instructed_values: rows whose generator wrote the whole text from an "
        "instruction (placement = whole). They are drawn at their own rate, outside the strata.",
    },
    "instructed_values": {
        "type": "array",
        "items": {"type": "string", "minLength": 1},
        "minItems": 1,
        "uniqueItems": True,
        "title": "Instructed rows: values",
        "x-advanced": True,
    },
    "instructed_rate": {
        "type": "number",
        "minimum": 0,
        "maximum": 1,
        "title": "Instructed rows: share",
        "x-advanced": True,
        "x-hint": "Empty: SECOND_OPINION_INSTRUCTED_RATE when the build starts (recorded).",
    },
    "instructed_min": {
        "type": "integer",
        "minimum": 0,
        "title": "Instructed rows: at least",
        "x-advanced": True,
        "x-hint": "Empty: SECOND_OPINION_INSTRUCTED_MIN when the build starts (recorded).",
    },
}
STRATIFIED_ONLY = (
    "strata",
    "per_stratum",
    "always_keep_column",
    "instructed_column",
    "instructed_values",
    "instructed_rate",
    "instructed_min",
)


def check_params_v11(params: Mapping[str, Any]) -> list[str]:
    mode = params.get("mode") or "count"
    if mode != STRATIFIED:
        given = [k for k in STRATIFIED_ONLY if params.get(k) is not None]
        return check_params(params) + [
            f"/{k}: {k} is for the stratified mode only; remove it when sampling by {mode}."
            for k in given
        ]
    problems = [
        f"/{k}: remove {k} when sampling by stratum; per_stratum sets the size."
        for k in ("count", "fraction")
        if params.get(k) is not None
    ]
    for needed in ("strata", "per_stratum"):
        if params.get(needed) is None:
            problems.append(f"/{needed}: the stratified mode needs {needed}.")
    if (params.get("instructed_column") is None) != (params.get("instructed_values") is None):
        problems.append("/instructed_values: name the instructed column and its values together.")
    return problems


class SampleV11:
    """``sample@1.1.0``: 1.0.0's count and fraction modes unchanged, plus ``stratified``.

    Stratified (FR-017.38): rows are taken in (row key, occurrence) order; every row whose
    ``always_keep_column`` is true is kept (a null or false is not) and counts toward no target;
    rows whose ``instructed_column`` holds one of ``instructed_values`` are drawn at
    ``instructed_rate`` of them, at least ``instructed_min`` (all when fewer), outside the strata;
    every other row is drawn per stratum (the values of ``strata``; a null is its own value), up
    to ``per_stratum`` each, seeded. A stratum with fewer rows is kept whole and reported short.
    Every row left out has a ``not_sampled`` drop event naming its stratum and rank.
    """

    manifest = manifest(
        "sample",
        "selector",
        "Keeps a seeded random sample of the rows: by count, by share, or by stratum (up to a "
        "number of rows per combination of the strata columns, every row a column marks, and "
        "instructed rows at their rate). Every row left out is logged with its rank in the "
        "draw. The same seed draws the same rows.",
        scope="dataset",
        params=PARAMS_V11,
        version="1.1.0",
    )

    def check_params(self, params: Mapping[str, Any]) -> list[str]:
        return check_params_v11(params)

    def columns_for(self, params: Mapping[str, Any]) -> tuple[list[str], list[str], list[str]]:
        reads = [str(c) for c in (params.get("strata") or [])]
        for name in ("always_keep_column", "instructed_column"):
            if params.get(name):
                reads.append(str(params[name]))
        return reads, [], []

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        if (params.get("mode") or "count") != STRATIFIED:
            return Sample().run(batch, params, ctx)
        return self._stratified(batch, params, ctx)

    def _stratified(
        self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext
    ) -> OperatorResult:
        from ...errors import StepFailed

        table = key_order(read_all_or_empty(ctx, batch))
        names = table.schema.names
        strata = [str(c) for c in params["strata"]]
        always = params.get("always_keep_column")
        instructed = params.get("instructed_column")
        for column in [*strata, *[c for c in (always, instructed) if c]]:
            if column not in names:
                raise StepFailed(
                    "column_not_found",
                    f"sample@1.1.0 reads {column!r}, which the input does not have.",
                    {"column": column},
                )
        if instructed and params.get("instructed_rate") is None:
            raise StepFailed(
                "params_invalid",
                "The instructed rows' rate was not filled in: the build resolves it from "
                "SECOND_OPINION_INSTRUCTED_RATE before the step runs.",
                {"param": "instructed_rate"},
            )
        n = table.num_rows
        rows = table.select(
            [*dict.fromkeys([*strata, *[c for c in (always, instructed) if c]])]
        ).to_pylist()
        seed = int(params["seed"]) if params.get("seed") is not None else int(ctx.step_seed)
        rng = np.random.default_rng(seed)
        values = {str(v) for v in (params.get("instructed_values") or [])}
        keep = np.zeros(n, dtype=bool)
        why: dict[int, str] = {}
        rank_of = np.full(n, -1, dtype=np.int64)
        always_kept = 0
        instructed_rows: list[int] = []
        pools: dict[str, list[int]] = {}
        for i, row in enumerate(rows):
            if always and row.get(always) is True:
                keep[i] = True
                always_kept += 1
            elif instructed and str(row.get(instructed)) in values:
                instructed_rows.append(i)
            else:
                pools.setdefault(stratum_of(row, strata), []).append(i)

        def draw(pool: list[int], k: int, label: str) -> None:
            order = rng.permutation(len(pool))
            for rank, j in enumerate(order.tolist()):
                index = pool[j]
                rank_of[index] = rank
                if rank < k:
                    keep[index] = True
                else:
                    why[index] = label

        instructed_report: dict[str, Any] | None = None
        if instructed:
            rate = float(params["instructed_rate"])
            floor = int(params.get("instructed_min") or 0)
            available = len(instructed_rows)
            k = min(available, max(int(np.ceil(rate * available)), floor))
            draw(instructed_rows, k, f"instructed rows: {k:,} of {available:,} drawn")
            instructed_report = {
                "column": instructed,
                "values": sorted(values),
                "rate": rate,
                "minimum": floor,
                "available": available,
                "kept": k,
            }
        target = int(params["per_stratum"])
        per_stratum: dict[str, dict[str, Any]] = {}
        for name in sorted(pools):
            pool = pools[name]
            k = min(target, len(pool))
            draw(pool, k, f"stratum {name}: {k:,} of {len(pool):,} drawn")
            per_stratum[name] = {
                "available": len(pool),
                "target": target,
                "kept": k,
                "short": len(pool) < target,
            }
        keys = table.column("_dw_row_key").to_pylist() if n else []
        occ = table.column("_dw_occurrence").to_pylist() if n else []
        events = [
            ctx.drop(
                (keys[i], occ[i]),
                "not_sampled",
                f"not drawn ({why[i]}, seed {seed})",
                "sample_rank",
                float(rank_of[i]),
            )
            for i in np.flatnonzero(~keep).tolist()
        ]
        report = {
            "mode": STRATIFIED,
            "seed": seed,
            "seed_source": "parameter" if params.get("seed") is not None else "step_seed",
            "rows_in": n,
            "rows_kept": int(keep.sum()),
            "rows_dropped": int(n - keep.sum()),
            "strata": strata,
            "per_stratum": per_stratum,
            "strata_short": sorted(k for k, v in per_stratum.items() if v["short"]),
            "always_keep_column": always,
            "always_kept": always_kept,
            "instructed": instructed_report,
        }
        return OperatorResult(output=table.filter(pa.array(keep)), events=events, report=report)


OPERATORS = (Sample, SampleV11)
