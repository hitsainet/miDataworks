"""Binning for the audit: quantile bands, the length band, top-N display values (FTID 004 §7.2).

Pure. Numeric columns are cut at their deciles (unique edges) so "a number" becomes a handful of
values the majority-by-value predictor can use; the edges are recorded so a band can be named and
its rows found again. Nulls are their own value everywhere (FR-004 §2.3), shown as "(empty)".
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np

#: Display name for the null value, everywhere a value is shown.
NULL_LABEL = "(empty)"
#: Bands for a binned numeric column (deciles).
DEFAULT_BANDS = 10
#: Values shown per column; the audit itself uses every value.
TOP_VALUES = 50


def quantile_edges(values: np.ndarray, bands: int = DEFAULT_BANDS) -> list[float]:
    """Interior cut points at the quantiles of the non-null values, unique and sorted."""
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return []
    qs = np.quantile(finite, [i / bands for i in range(1, bands)])
    return sorted({float(q) for q in qs})


def band_index(values: np.ndarray, edges: Sequence[float]) -> np.ndarray:
    """Band per value (0..len(edges)); non-finite (null) values get band ``len(edges) + 1``."""
    out = np.searchsorted(np.asarray(edges, dtype=np.float64), values, side="right").astype(
        np.int64
    )
    out[~np.isfinite(values)] = len(edges) + 1
    return out


def band_label(index: int, edges: Sequence[float]) -> str:
    """A band's display name: ``< e0``, ``[e0, e1)``, ``>= eN``, or the null label."""
    if index == len(edges) + 1:
        return NULL_LABEL
    if not edges:
        return "all"
    if index == 0:
        return f"< {_fmt(edges[0])}"
    if index == len(edges):
        return f">= {_fmt(edges[-1])}"
    return f"[{_fmt(edges[index - 1])}, {_fmt(edges[index])})"


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.4g}"


def content_length(value: Any) -> int:
    """Characters of one content cell; chat content sums its messages' ``content`` (FTID §7.2)."""
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        total = 0
        for item in value:
            if isinstance(item, dict):
                content = item.get("content")
                total += len(content) if isinstance(content, str) else 0
            elif isinstance(item, str):
                total += len(item)
        return total
    return len(str(value))


def top_values(per_value: dict[str, dict[str, int]], n: int = TOP_VALUES) -> dict[str, Any]:
    """The ``n`` largest values for display, plus an ``other`` bucket summing the rest."""
    ordered = sorted(per_value.items(), key=lambda kv: (-sum(kv[1].values()), kv[0]))
    shown = ordered[:n]
    rest = ordered[n:]
    other: dict[str, int] = {}
    for _, counts in rest:
        for label, count in counts.items():
            other[label] = other.get(label, 0) + count
    return {
        "values": [{"value": v, "counts_by_label": c} for v, c in shown],
        "other": other if rest else None,
        "other_values": len(rest),
    }


def is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))
