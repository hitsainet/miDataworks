"""A miStudio probe report -> per-set figures (FR-009.33 - FR-009.36, FR-009.39, FR-009.41;
FTDD 009 section 5.5; FTID 009 section 7.4).

Rules that make every number traceable to recorded IDs:

- evaluations map to roles ONLY by the probe dataset ID 009 registered (``role_by_view``); an
  evaluation of a view this send did not register is kept under ``not_from_this_set``;
- firing rates come from ``report.threshold_transfer.per_set``, keyed by the set NAME, which is the
  view name 009 wrote at registration (``view_name_by_id``);
- the rung is miStudio's ``rung_language`` and ``rung_next_step``, copied verbatim. This module never
  maps a rung number to words (miStudio forbids it, ``ProbeMonitorSummary`` comment);
- caveats are copied verbatim; judge runs are listed as "compared with a judge".

Report fields are read directly (``report["evaluations"]``), never through a defaulting ``get``: a
renamed field must fail loudly (FTID 009 section 14).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

JUDGE_WORDING = "compared with a judge"


def _rung_text(report: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """The report's rung strings: top level first (where miStudio writes them), then the probe."""
    language = report["rung_language"] or report["probe"]["rung_language"] or None
    next_step = report["rung_next_step"] or report["probe"]["rung_next_step"] or None
    return language, next_step


def figures_from_report(
    report: Mapping[str, Any],
    role_by_view: Mapping[str, Mapping[str, Any]],
    view_name_by_id: Mapping[str, str],
) -> dict[str, Any]:
    """Figures for one probe. ``role_by_view``: probe dataset ID -> {role, role_id, version_id,
    split, name}; ``view_name_by_id``: probe dataset ID -> the view name 009 registered."""
    probe = report["probe"]
    transfer = report["threshold_transfer"] or {}
    per_set_by_name = {str(s["name"]): s for s in (transfer.get("per_set") or [])}
    language, next_step = _rung_text(report)
    sets: list[dict[str, Any]] = []
    not_from_set: list[dict[str, Any]] = []
    for evaluation in report["evaluations"]:
        view_id = str(evaluation["dataset_id"])
        metrics = evaluation["metrics"] or {}
        ci = metrics.get("ci") or {}
        entry: dict[str, Any] = {
            "probe_dataset_id": view_id,
            "status": evaluation["status"],
            "auroc": metrics.get("auroc"),
            "ci": [ci.get("low"), ci.get("high")] if ci else None,
            "n_positive": evaluation["n_positive"],
            "n_negative": evaluation["n_negative"],
            "out_of_distribution": metrics.get("out_of_distribution"),
            "set_name": metrics.get("name"),
        }
        role = role_by_view.get(view_id)
        if role is None:
            entry["label"] = "not from this set"
            not_from_set.append(entry)
            continue
        entry.update(
            {
                "role": role["role"],
                "role_id": role["role_id"],
                "version_id": role["version_id"],
                "split": role["split"],
                "view_name": view_name_by_id.get(view_id),
            }
        )
        # miStudio names each set by its view name, which 009 wrote: no other key is tried.
        firing = per_set_by_name.get(str(view_name_by_id.get(view_id)))
        entry["firing"] = (
            {
                "positives_firing": firing["recall_at_shipped"],
                "negatives_firing": firing["fpr_at_shipped"],
                "unreachable": bool(firing["unreachable"]),
                "n_positive": evaluation["n_positive"],
                "n_negative": evaluation["n_negative"],
            }
            if firing is not None
            else None
        )
        sets.append(entry)
    return {
        "probe_id": probe["id"],
        "run_id": probe["run_id"],
        "layer": probe["layer"],
        "rule": probe["rule"],
        "rule_params": probe["rule_params"],
        "selected": bool(probe["selected"]),
        "threshold": probe["threshold"],
        "target_fpr": probe["target_fpr"],
        "realised_fpr": probe["realised_fpr"],
        "threshold_source": probe["threshold_source"],
        "rung": probe["rung"],
        "rung_language": language,
        "rung_next_step": next_step,
        "sets": sets,
        "not_from_this_set": not_from_set,
        "caveats": {
            "validation_caveat": report["validation_caveat"],
            "threshold_transfer_caution": transfer.get("caution"),
        },
        "judge_runs": [{**dict(j), "wording": JUDGE_WORDING} for j in (report["judge_runs"] or [])],
    }
