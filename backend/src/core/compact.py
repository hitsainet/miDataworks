"""Summary views of large read results (e2e run G9, 2026-10-21).

``run_shortcut_audit`` answered 688,609 characters, ``check_detector_set`` about 25 K tokens per
call with its histograms repeated, ``list_jobs(finished)`` 180 K characters: each overflowed or
nearly overflowed an agent's budget. A route that can return that much takes ``detail``:
``full`` (the whole answer, unchanged) or ``summary`` (every verdict, figure and reason kept; long
lists and big objects cut to their first entries with a note of what was left out and how to get
it). The MCP tools ask for ``summary`` by default.
"""

from __future__ import annotations

from typing import Any, Literal

Detail = Literal["full", "summary"]

#: A list or object longer than this is cut in a summary.
SUMMARY_ITEMS = 12
OMITTED = "_omitted"


def compact(value: Any, *, items: int = SUMMARY_ITEMS) -> Any:
    """``value`` with every list cut to ``items`` entries and every object to ``items`` keys,
    recursively; a cut leaves ``{"_omitted": n, "detail": "full"}`` in place of what it dropped.
    Scalars are kept as they are."""
    if isinstance(value, dict):
        keys = list(value)
        out = {k: compact(value[k], items=items) for k in keys[:items]}
        if len(keys) > items:
            out[OMITTED] = {"keys": len(keys) - items, "ask": "detail=full"}
        return out
    if isinstance(value, list):
        out_list = [compact(v, items=items) for v in value[:items]]
        if len(value) > items:
            out_list.append({OMITTED: len(value) - items, "ask": "detail=full"})
        return out_list
    return value


#: Per-column fields of a shortcut audit that a summary drops: the per-value tables and bin edges
#: (thousands of entries for a column with a value per row). Each column's figure, control,
#: validity and value count stay.
AUDIT_BULK = ("per_value", "bins")


def summarise_audit(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return result
    out = dict(result)
    columns = []
    for column in result.get("columns") or []:
        kept = {k: v for k, v in column.items() if k not in AUDIT_BULK}
        kept["omitted"] = [k for k in AUDIT_BULK if k in column]
        columns.append(kept)
    out["columns"] = sorted(columns, key=lambda c: -(c.get("figure") or 0.0))
    for key, value in list(out.items()):
        if key != "columns":
            out[key] = compact(value, items=40)
    out["detail"] = "summary: per-column per_value and bins omitted; detail=full for them"
    return out


def summarise_checks(result: dict[str, Any]) -> dict[str, Any]:
    """D-1 to D-9 verdicts with reasons and next steps kept whole; the profiles, label values and
    histograms that repeat across outcomes are cut."""
    out: dict[str, Any] = {
        "set_id": result.get("set_id"),
        "send_allowed": result.get("send_allowed"),
        "first_refusal": compact(result.get("first_refusal")),
        "outcomes": [
            {
                **{k: v for k, v in o.items() if k != "details"},
                "details": compact(o.get("details")),
            }
            for o in result.get("outcomes") or []
        ],
        "expected_counts": compact(result.get("expected_counts")),
        "overlap_min": result.get("overlap_min"),
        "omitted": [
            k for k in ("label_values", "profiles", "profiles_words", "monitored") if k in result
        ],
        "detail": "summary: profiles, label values and long details cut; detail=full for them",
    }
    return out


#: Job fields a summary drops: the request parameters and the result payload.
JOB_BULK = ("params", "result")
