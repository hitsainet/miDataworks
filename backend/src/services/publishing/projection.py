"""What a publish build ships of a version (FR-008.27, FR-008.28; FTID 008 section 7.1).

- user columns plus ``_dw_row_key``, ``_dw_occurrence`` and ``_dw_origin`` (so a consumer can
  recompute 002's logical digest without knowing the key normalisation, T-38); every other
  ``_dw_*`` column is dropped;
- effective labels through 006's resolver (``dw.effective-label/v1``): an ``overridden`` row takes
  the operator's label, a ``flagged_unresolved`` row is omitted, a ``model`` row keeps its label.
  The state machine — including "an agent decision never wins" (P-10) — is 006's; this module
  applies the states the resolver returns and nothing else;
- version row order is preserved; held-out splits stay separate and are marked
  ``evaluation_only`` (FR-008.28).

Without 006 no review decision can exist, so the projection ships the version's labels unchanged
and says so in the projection specification (``review_layer: false``), which is part of the build's
content address: when 006 lands, earlier builds are not reused for the new projection.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import pyarrow as pa

from ..identity import digest_text

RESOLVER_ID = "dw.effective-label/v1"
SYSTEM_KEEP: tuple[str, ...] = ("_dw_row_key", "_dw_occurrence", "_dw_origin")

#: Column names TRL and the contract give a meaning; anything else is "other".
SEMANTIC_BY_NAME: dict[str, str] = {
    "text": "text",
    "messages": "messages",
    "prompt": "prompt",
    "completion": "completion",
    "completions": "completions",
    "chosen": "chosen",
    "rejected": "rejected",
    "labels": "labels",
    "reference": "reference",
}


class ProjectionError(ValueError):
    def __init__(self, message: str, code: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details


def kept_columns(schema_names: Sequence[str]) -> list[str]:
    """User columns in file order, then the three system columns 008 ships."""
    user = [n for n in schema_names if not n.startswith("_dw_")]
    missing = [c for c in SYSTEM_KEEP if c not in schema_names]
    if missing:
        raise ProjectionError(
            f"The version's split file has no {missing[0]} column; it was not written by 002.",
            "version_file_malformed",
            missing=missing,
        )
    return user + list(SYSTEM_KEEP)


def projection_spec(
    version_id: str, label_column: str | None, *, review_layer: bool
) -> dict[str, Any]:
    return {
        "format": "dw.publish-projection/v1",
        "version_id": version_id,
        "label_resolver": RESOLVER_ID,
        "label_column": label_column,
        "review_layer": review_layer,
    }


def projection_digest(spec: Mapping[str, Any]) -> str:
    return digest_text(dict(spec))


@dataclass
class Counters:
    omitted_excluded: int = 0
    omitted_flagged_unresolved: int = 0
    overrides_applied: int = 0
    label_counts: dict[str, int] = field(default_factory=dict)
    #: Kept rows whose label is null. Counted apart from ``label_counts`` (whose keys are the
    #: manifest's label values), but counted: they ship in the file, and a consumer maps them.
    null_labels: int = 0


def label_text(value: Any) -> str:
    """A label value as the card and manifest print it (booleans in lower case, like JSON)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def apply_effective_labels(
    batch: pa.RecordBatch,
    label_column: str | None,
    resolved: Mapping[str, Mapping[str, Any]] | None,
    counters: Counters,
) -> pa.RecordBatch:
    """Overrides replace the label, flagged-unresolved rows are omitted, others pass unchanged.

    ``resolved`` maps a row key to 006's ``EffectiveLabel`` ``{label, state, decision_id}``; None
    means 006 is absent and no decision can exist.
    """
    if label_column is None or resolved is None:
        if label_column is not None:
            for value in batch.column(label_column).to_pylist():
                if value is not None:
                    key = label_text(value)
                    counters.label_counts[key] = counters.label_counts.get(key, 0) + 1
                else:
                    counters.null_labels += 1
        return batch
    keys = batch.column("_dw_row_key").to_pylist()
    labels = batch.column(label_column).to_pylist()
    keep: list[bool] = []
    new_labels: list[Any] = []
    for key, label in zip(keys, labels, strict=True):
        state = resolved.get(key)
        if state is not None and state["state"] == "flagged_unresolved":
            counters.omitted_flagged_unresolved += 1
            keep.append(False)
            continue
        if state is not None and state["state"] == "overridden":
            counters.overrides_applied += 1
            label = state["label"]
        keep.append(True)
        new_labels.append(label)
        if label is not None:
            text = label_text(label)
            counters.label_counts[text] = counters.label_counts.get(text, 0) + 1
        else:
            counters.null_labels += 1
    filtered = batch.filter(pa.array(keep, type=pa.bool_()))
    index = filtered.schema.get_field_index(label_column)
    field_type = filtered.schema.field(index).type
    column = pa.array(new_labels, type=field_type)
    arrays = [column if i == index else filtered.column(i) for i in range(filtered.num_columns)]
    return pa.RecordBatch.from_arrays(arrays, schema=filtered.schema)


def column_semantic(name: str, label_column: str | None) -> str:
    if label_column is not None and name == label_column:
        return "label"
    return SEMANTIC_BY_NAME.get(name, "other")


def describe_columns(
    schema: pa.Schema,
    roles: Mapping[str, str],
    label_column: str | None,
    label_values: Sequence[str] | None,
) -> list[dict[str, Any]]:
    """The manifest's column list (M-9) for the shipped schema."""
    out: list[dict[str, Any]] = []
    for f in schema:
        role = "system" if f.name.startswith("_dw_") else roles.get(f.name, "metadata")
        semantic = column_semantic(f.name, label_column)
        out.append(
            {
                "name": f.name,
                "arrow_type": str(f.type),
                "role": role,
                "semantic": semantic,
                "label_values": sorted(label_values or []) if semantic == "label" else None,
                "extensions": {},
            }
        )
    return out
