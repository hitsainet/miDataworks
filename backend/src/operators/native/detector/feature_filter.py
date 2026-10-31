"""``feature_filter@1`` (009 FR-009.66, FR-009.67; FTID 009 section 7.7).

Keeps or drops rows by the activation of named SAE features, read from a COMPLETED feature-tag
label run (005's ``millm_sae_features`` protocol, run kind ``feature_tag``) through a ``label_run``
binding — so a build never calls miLLM live (002 FR-002.2). 003's threshold control applies:
``min_activation`` cuts the statistic ``activation`` (drop below).

A row's ``activation`` is the largest value any named feature reached at any recorded position.
A tag run records only each position's top ``k`` features, so a named feature missing at a
position is known only to be at or below that position's smallest reported value:

- when that bound is below ``min_activation``, the feature is below the cutoff there;
- otherwise its activation is UNKNOWN, and the row is dropped as ``activation_unknown`` (raise the
  tag run's ``top_k`` or name the feature in its ``features``). It is never read as zero.

Every drop records the feature index and activation as its statistic (FR-009.66). A row the tag
run did not tag (skipped, or absent) is dropped as ``not_tagged``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pyarrow as pa

from ...context import RunContext
from ...manifest import OperatorManifest, ResourceSpec, ThresholdSpec
from ...protocol import OperatorResult
from ..curation.common import pairs, schema
from .common import bound, published_labels

PARAMS: dict[str, Any] = {
    "feature_tag_run_id": {"type": "string", "minLength": 1, "title": "Feature-tag run"},
    "features": {
        "type": "array",
        "items": {"type": "integer", "minimum": 0},
        "minItems": 1,
        "title": "SAE feature indices",
    },
    "min_activation": {
        "type": "number",
        "title": "Keep at or above (SAE activation)",
        "x-unit": "activation",
    },
}

MANIFEST = OperatorManifest(
    name="feature_filter",
    version="1",
    provider="native",
    provider_version="009-1",
    kind="filter",
    scope="row",
    description=(
        "Keeps rows where a named SAE feature reached at least the cutoff, read from a completed "
        "feature-tag run (unsteered, miLLM scoring mode)."
    ),
    params_schema=schema(PARAMS, ("feature_tag_run_id", "features", "min_activation")),
    thresholds=(
        ThresholdSpec(
            param="min_activation", statistic="activation", unit="activation", drop_when="below"
        ),
    ),
    resources=ResourceSpec(queue="curation"),
    deterministic=True,
    binding_kinds=("label_run",),
)


def row_activation(
    positions: Sequence[Mapping[str, Any]], features: Sequence[int], cutoff: float
) -> tuple[float | None, int | None, bool]:
    """(activation, feature index, known): the largest named-feature value over the positions;
    ``known`` is False when a missing feature could still reach ``cutoff``."""
    wanted = {int(f) for f in features}
    best: float | None = None
    best_feature: int | None = None
    known = True
    for position in positions:
        listed = {int(f["index"]): float(f["value"]) for f in position["features"]}
        for feature in wanted:
            if feature in listed:
                if best is None or listed[feature] > best:
                    best, best_feature = listed[feature], feature
            elif listed and min(listed.values()) >= cutoff:
                known = False  # below the k-th value, which itself clears the cutoff
            elif not listed:
                known = False
    return best, best_feature, known


class FeatureFilter:
    manifest = MANIFEST

    def _tags(self, params: Mapping[str, Any], ctx: RunContext) -> dict[str, dict[str, Any]]:
        run_id = str(params["feature_tag_run_id"])
        bound(ctx, run_id)
        return published_labels(run_id)

    def compute_statistics(
        self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext
    ) -> dict[str, pa.Array]:
        tags = self._tags(params, ctx)
        cutoff = float(params.get("min_activation", 0.0))
        values: list[float | None] = []
        for key in batch.column("_dw_row_key").to_pylist():
            parsed = (tags.get(str(key)) or {}).get("parsed_value") or {}
            activation, _, known = row_activation(
                parsed.get("positions") or [], params["features"], cutoff
            )
            values.append(activation if known else None)
        return {"activation": pa.array(values, pa.float64())}

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        tags = self._tags(params, ctx)
        cutoff = float(params["min_activation"])
        keep: list[int] = []
        events = []
        ids = pairs(batch)
        for i, (key, _) in enumerate(ids):
            tag = tags.get(key)
            if tag is None or tag["outcome"] != "tagged":
                events.append(
                    ctx.drop(ids[i], "not_tagged", "the tag run has no tags for this row",
                             "activation", None, text="not tagged")
                )  # fmt: skip
                continue
            activation, feature, known = row_activation(
                tag["parsed_value"]["positions"], params["features"], cutoff
            )
            if activation is not None and activation >= cutoff:
                keep.append(i)
                continue
            if not known:
                events.append(
                    ctx.drop(
                        ids[i],
                        "activation_unknown",
                        "a named feature is outside this row's recorded top features, which all "
                        "clear the cutoff; raise the tag run's top_k or name the feature",
                        "activation",
                        activation,
                        cutoff,
                        ">=",
                        text=f"feature={feature} activation={activation}",
                    )
                )
                continue
            events.append(
                ctx.drop(
                    ids[i],
                    "below_activation",
                    (
                        f"feature {feature} reached {activation}, below {cutoff}"
                        if feature is not None
                        else f"no named feature is among the recorded top features (cutoff {cutoff})"
                    ),
                    "activation",
                    activation,
                    cutoff,
                    ">=",
                    text=f"feature={feature} activation={activation}",
                )
            )
        return OperatorResult(output=batch.take(pa.array(keep, pa.int64())), events=events)


OPERATORS: tuple[type, ...] = (FeatureFilter,)
