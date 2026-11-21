"""``hard_negative_miner@1`` (009 FR-009.57 - FR-009.59; FTID 009 section 7.7).

Keeps rows a probe scores near its threshold (``near``), rows it gets wrong (``wrong``), or rows
that are either (``both``), reading a COMPLETED probe-verdict label run through a ``label_run``
binding (the run's published ``labels.parquet``; an operator opens no database session). The
reference for "wrong" is another label run or a label column with its positive and negative
values (FR-009.58: "gets wrong" names its reference).

Every DROPPED row's event carries the score, the threshold and their distance (``score -
threshold``) as its statistic; every KEPT row's are listed in the step report, since a selector
adds no columns (003's kind effects). A row the probe did not score (skipped, provisional, or
absent from the run) is dropped as ``not_scored`` — a provisional verdict never decides
"wrong" (P-20). A cap keeps the rows closest to the bar, ties by row key.

"Mined from evaluation data" (FR-009.59) is decided by 009's checks from the recorded lineage
(``services/detector_sets/mining.py``), not here: this operator cannot see detector sets.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pyarrow as pa

from ...context import RunContext
from ...errors import OperatorError
from ...manifest import OperatorManifest, ResourceSpec
from ...protocol import OperatorResult
from ..curation.common import key_order, pairs, read_all, schema
from .common import bound, published_labels, reference_from_column

PARAMS: dict[str, Any] = {
    "probe_label_run_id": {"type": "string", "minLength": 1, "title": "Probe-verdict run"},
    "mode": {"type": "string", "enum": ["near", "wrong", "both"], "title": "Keep"},
    "band_below": {
        "type": "number",
        "minimum": 0,
        "title": "Band below the threshold (probe score)",
        "x-unit": "probe score",
    },
    "band_above": {
        "type": "number",
        "minimum": 0,
        "title": "Band above the threshold (probe score)",
        "x-unit": "probe score",
    },
    "reference_kind": {
        "type": "string",
        "enum": ["label_run", "label_column"],
        "title": "What counts as right",
    },
    "reference_label_run_id": {"type": "string", "minLength": 1, "title": "Reference label run"},
    "reference_column": {"type": "string", "minLength": 1, "title": "Reference label column"},
    "reference_positive_values": {"type": "array", "items": {"type": "string"}},
    "reference_negative_values": {"type": "array", "items": {"type": "string"}},
    "cap": {"type": "integer", "minimum": 1, "title": "At most this many rows"},
}

MANIFEST = OperatorManifest(
    name="hard_negative_miner",
    version="1",
    provider="native",
    provider_version="009-1",
    kind="selector",
    scope="dataset",
    description=(
        "Keeps rows a probe scores near its threshold, rows it gets wrong, or either, from a "
        "completed probe-verdict run. Each row records its score, threshold and distance."
    ),
    params_schema=schema(PARAMS, ("probe_label_run_id", "mode")),
    resources=ResourceSpec(queue="curation", cpu_class="light", memory_class="medium"),
    deterministic=True,
    binding_kinds=("label_run",),
)


def _reference(
    params: Mapping[str, Any], table: pa.Table, keys: list[str]
) -> dict[str, bool] | None:
    kind = params.get("reference_kind")
    if kind is None:
        return None
    if kind == "label_run":
        run_id = params.get("reference_label_run_id")
        if not run_id:
            raise OperatorError("reference_invalid", "Name the reference label run.", {})
        labels = published_labels(str(run_id))
        return {
            k: v["outcome"] == "positive"
            for k, v in labels.items()
            if v["outcome"] in ("positive", "negative") and not v["provisional"]
        }
    column = params.get("reference_column")
    if not column or column not in table.schema.names:
        raise OperatorError(
            "reference_invalid",
            f"The reference column {column!r} is not in the input.",
            {"column": column},
        )
    values = dict(zip(keys, table.column(column).to_pylist(), strict=True))
    return reference_from_column(
        values,
        list(params.get("reference_positive_values") or []),
        list(params.get("reference_negative_values") or []),
    )


def distance(score: float, threshold: float) -> float:
    """Signed distance to the bar: positive above it (where a score fires, P-03)."""
    return score - threshold


def near(d: float, below: float, above: float) -> bool:
    return -below <= d <= above


class HardNegativeMiner:
    manifest = MANIFEST

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        run_id = str(params["probe_label_run_id"])
        bound(ctx, run_id)
        mode = str(params["mode"])
        band = (
            {"below": float(params["band_below"]), "above": float(params["band_above"])}
            if params.get("band_below") is not None and params.get("band_above") is not None
            else None
        )
        if mode in ("near", "both") and band is None:
            raise OperatorError(
                "band_required", f"Mode {mode!r} needs band_below and band_above.", {"mode": mode}
            )
        if mode in ("wrong", "both") and params.get("reference_kind") is None:
            raise OperatorError(
                "reference_required", f"Mode {mode!r} needs a reference.", {"mode": mode}
            )
        table = key_order(read_all(ctx))
        ids = pairs(table)
        keys = [k for k, _ in ids]
        probe = published_labels(run_id)
        reference = _reference(params, table, keys)
        events = []
        kept_idx: list[int] = []
        kept_stats: list[dict[str, Any]] = []
        for i, (key, _) in enumerate(ids):
            label = probe.get(key)
            parsed = (label or {}).get("parsed_value") or {}
            score, threshold = parsed.get("score"), parsed.get("threshold")
            usable = (
                label is not None
                and not label["provisional"]
                and label["outcome"] in ("positive", "negative")
                and score is not None
                and threshold is not None
            )
            if not usable or score is None or threshold is None:
                events.append(
                    ctx.drop(
                        ids[i],
                        "not_scored",
                        "the probe gave no usable verdict for this row "
                        f"({(label or {}).get('outcome', 'absent')})",
                        "distance",
                        None,
                        text="not scored",
                    )
                )
                continue
            d = distance(float(score), float(threshold))
            stat = {"score": float(score), "threshold": float(threshold), "distance": d}
            text = f"score={score} threshold={threshold} distance={d}"
            is_near = band is not None and near(d, float(band["below"]), float(band["above"]))
            is_wrong: bool | None = None
            if reference is not None and key in reference:
                is_wrong = (label["outcome"] == "positive") != reference[key]  # type: ignore[index]
            keep = (
                is_near
                if mode == "near"
                else bool(is_wrong) if mode == "wrong" else is_near or bool(is_wrong)
            )
            if keep:
                kept_idx.append(i)
                kept_stats.append({"row_key": key, **stat, "wrong": is_wrong, "near": is_near})
                continue
            if mode == "wrong" and is_wrong is None:
                code, why = "no_reference", "the reference has no label for this row"
            elif mode == "near":
                code, why = "outside_band", f"distance {d:.4f} lies outside the band"
            else:
                code, why = (
                    "correct",
                    f"the probe is right and distance {d:.4f} is outside the band",
                )
            events.append(
                ctx.drop(ids[i], code, why, "distance", d, dict(band or {}), "band", text=text)
            )
        cap = params.get("cap")
        if cap is not None and len(kept_idx) > int(cap):
            order = sorted(
                range(len(kept_idx)),
                key=lambda j: (abs(kept_stats[j]["distance"]), kept_stats[j]["row_key"]),
            )
            keep_set = set(order[: int(cap)])
            for j in sorted(set(range(len(kept_idx))) - keep_set):
                s = kept_stats[j]
                events.append(
                    ctx.drop(
                        ids[kept_idx[j]],
                        "over_cap",
                        f"over the cap of {cap} rows; {abs(s['distance']):.4f} from the bar",
                        "distance",
                        s["distance"],
                        int(cap),
                        "cap",
                        text=f"score={s['score']} threshold={s['threshold']} distance="
                        f"{s['distance']}",
                    )
                )
            kept_idx = [kept_idx[j] for j in sorted(keep_set)]
            kept_stats = [kept_stats[j] for j in sorted(keep_set)]
        output = table.take(pa.array(kept_idx, pa.int64()))
        return OperatorResult(
            output=output,
            events=events,
            report={
                "probe_label_run_id": run_id,
                "mode": mode,
                "band": band,
                "cap": cap,
                "rows_in": table.num_rows,
                "rows_kept": output.num_rows,
                "kept": kept_stats,
            },
        )


OPERATORS: tuple[type, ...] = (HardNegativeMiner,)
