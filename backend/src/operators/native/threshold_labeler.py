"""The native Threshold labeler (FR-005.23; FTDD 005 section 6.8).

Applies FR-005.18's two-threshold rule to a COMPLETED label run inside a recipe, under 003's
``Operator`` interface. It reads the run's published ``labels.parquet`` (an operator never opens a
database session, 003 ``RunContext``); the build binds the run (``binding_kinds = ("label_run",)``)
so 002 refuses an unknown or unfinished run before this step runs.

Output columns ``label`` and ``label_probability``. With ``drop_excluded`` an excluded row is
dropped with reason ``excluded_by_band`` and statistic ``P``; otherwise it is kept, labeled
``excluded``. The decision is ``labeling_rules.decide_binary`` — called, never re-implemented.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from ...core.storage import resolve_under_data_dir
from ...services import labeling_rules
from ..context import RunContext
from ..errors import OperatorError
from ..manifest import ColumnSpec, OperatorManifest, ResourceSpec, ThresholdSpec
from ..protocol import OperatorResult

MANIFEST = OperatorManifest(
    name="threshold_labeler",
    version="1",
    provider="native",
    provider_version="005-1",
    kind="labeler",
    description=(
        "Labels each row from a completed label run's probability: positive at or above one "
        "threshold, negative at or below the other, excluded in between."
    ),
    output_columns=(
        ColumnSpec(name="label", type="string", role="metadata"),
        ColumnSpec(name="label_probability", type="float64", role="metadata", required=False),
    ),
    params_schema={
        "type": "object",
        "properties": {
            "label_run_id": {"type": "string", "title": "Label run", "minLength": 1},
            "threshold_positive": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "title": "Positive at or above",
                "x-unit": "probability",
            },
            "threshold_negative": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "title": "Negative at or below",
                "x-unit": "probability",
            },
            "drop_excluded": {"type": "boolean", "default": False, "title": "Drop excluded rows"},
        },
        "required": ["label_run_id", "threshold_positive", "threshold_negative"],
        "additionalProperties": False,
    },
    thresholds=(
        ThresholdSpec(
            param="threshold_positive",
            statistic="P",
            unit="probability",
            drop_when="below",
            pair_param="threshold_negative",
        ),
    ),
    resources=ResourceSpec(queue="curation"),
    deterministic=True,
    binding_kinds=("label_run",),
)


def _probabilities(label_run_id: str) -> dict[str, float | None]:
    path = resolve_under_data_dir("runs", label_run_id, "labels.parquet")
    if not path.is_file():
        raise OperatorError(
            "label_run_not_published",
            f"Label run {label_run_id} has no published labels; bind a completed run.",
            {"label_run_id": label_run_id},
        )
    table = pq.read_table(path, columns=["row_key", "probability"])
    keys = table.column("row_key").to_pylist()
    probs = table.column("probability").to_pylist()
    return dict(zip(keys, probs, strict=True))


class ThresholdLabeler:
    manifest = MANIFEST

    def compute_statistics(
        self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext
    ) -> dict[str, pa.Array]:
        probs = _probabilities(str(params["label_run_id"]))
        keys = batch.column("_dw_row_key").to_pylist()
        return {"P": pa.array([probs.get(k) for k in keys], pa.float64())}

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        try:
            labeling_rules.validate_thresholds(
                params.get("threshold_positive"), params.get("threshold_negative")
            )
        except labeling_rules.ThresholdsInvalid as exc:
            raise OperatorError("thresholds_invalid", str(exc), {"field": exc.field}) from None
        positive = float(params["threshold_positive"])
        negative = float(params["threshold_negative"])
        drop = bool(params.get("drop_excluded", False))
        values = self.compute_statistics(batch, params, ctx)["P"].to_pylist()
        keep: list[int] = []
        labels: list[str | None] = []
        kept_probs: list[float | None] = []
        events = []
        pairs = batch.select(["_dw_row_key", "_dw_occurrence"]).to_pylist()
        for i, p in enumerate(values):
            if p is None:
                label: str | None = None
            else:
                label = labeling_rules.decide_binary(p, positive, negative)
            if label == "excluded" and drop:
                events.append(
                    ctx.drop(
                        pairs[i],
                        "excluded_by_band",
                        f"P {p:.3f} lies between {negative} and {positive}",
                        "P",
                        p,
                        {"positive_at": positive, "negative_at": negative},
                        "band",
                    )
                )
                continue
            keep.append(i)
            labels.append(label)
            kept_probs.append(p)
        output = batch.take(pa.array(keep, pa.int64()))
        output = output.append_column("label", pa.array(labels, pa.string()))
        output = output.append_column("label_probability", pa.array(kept_probs, pa.float64()))
        return OperatorResult(
            output=output,
            events=events,
            output_roles={"label": "metadata", "label_probability": "metadata"},
        )


OPERATORS: tuple[type, ...] = (ThresholdLabeler,)
