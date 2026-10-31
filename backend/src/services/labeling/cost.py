"""What a label run costs, and when it may not run (017 FR-017.39, FR-017.40, FR-017.41; FTID §7.7,
§7.9; TD-017.43, TD-017.44; OQ-017-1 (a)).

**Paid.** A role is paid when the operator's price per call (``dw_endpoint_roles.
price_per_call_usd``, one row and one question) is above zero, or when it speaks a HOSTED protocol
and no price is entered (``HOSTED_PROTOCOLS``: fails closed on the one hosted protocol until the
operator enters a price, zero included). The price is the one of the role whose server the run
calls (a judge using the classifier's endpoint pays the classifier's price).

**Refused, never gated** (the plan and the start, for the operator and an agent alike):

- a paid role with no price: ``classifier_unpriced``;
- a paid run while ``CLASSIFIER_SPEND_CEILING_USD`` is unset, or whose estimate would take the
  total spent above it: ``classifier_spend_ceiling``;
- a paid run over ``CLASSIFIER_RUN_CAP_ROWS`` rows: ``classifier_cap_exceeded``.

**Spent** is what paid runs actually scored: per run with a paid cost basis, the rows it scored
(its labels, less the rows it reused) x its questions x its price. An agent's paid run waits for
the operator too (``paid_classifier_run``, ``api/v1/endpoints/label_runs.py``): this module only
decides "paid" and the money; the gate decides who approves.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from ...core.errors import AppError

HOSTED_PROTOCOLS: frozenset[str] = frozenset({"typesafe_systemone"})
#: One run asks one question per row (FR-017.39: one run per question, grouped by a hand-off).
QUESTIONS_PER_RUN = 1
NEXT_PRICE = {
    "tool": "dataworks_list_endpoint_roles",
    "args": {},
    "why": "the operator enters the role's price per call in Settings, on the endpoint's card (Price per call)",
}
NEXT_CEILING = {
    "tool": "dataworks_get_environment",
    "args": {},
    "why": "the operator sets CLASSIFIER_SPEND_CEILING_USD in Settings (an agent may not)",
}
NEXT_CAP = {
    "tool": "dataworks_plan_label_run",
    "args": {},
    "why": "plan a run over a selection within the cap (sample@1.1.0, mode stratified)",
}


class CostRefused(AppError):
    def __init__(self, code: str, message: str, details: dict[str, Any]) -> None:
        super().__init__(message, code=code, status_code=422, details=details)


def is_paid(protocol: str | None, price: Decimal | float | None) -> bool:
    if price is None:
        return protocol in HOSTED_PROTOCOLS
    return Decimal(str(price)) > 0


def basis(
    protocol: str | None,
    price: Decimal | float | None,
    rows: int,
    questions: int = QUESTIONS_PER_RUN,
) -> dict[str, Any]:
    """The cost basis a plan states and a run records: ``estimated_usd`` is None when unpriced."""
    paid = is_paid(protocol, price)
    estimate = None if price is None else float(Decimal(str(price)) * rows * questions)
    return {
        "paid": paid,
        "protocol": protocol,
        "price_per_call_usd": None if price is None else float(Decimal(str(price))),
        "questions": questions,
        "rows_planned": rows,
        "estimated_usd": estimate,
        "priced": price is not None,
    }


def check(
    cost: Mapping[str, Any], *, cap: int, ceiling: float | None, spent: float, role: str
) -> dict[str, Any]:
    """Refuse a paid run that may not start; returns the money facts the plan shows."""
    facts = {
        "cap_rows": cap,
        "ceiling_usd": ceiling,
        "spent_usd": round(spent, 6),
        "remaining_usd": None if ceiling is None else round(max(ceiling - spent, 0.0), 6),
    }
    if not cost["paid"]:
        return facts
    if not cost["priced"]:
        raise CostRefused(
            "classifier_unpriced",
            f"The {role} endpoint speaks {cost['protocol']}, a hosted (paid) protocol, and no "
            "price per call is entered. The operator enters it (zero included) in Settings, on the "
            f"{role} endpoint's card (Price per call), before any run on it.",
            {"role": role, "protocol": cost["protocol"], "next": NEXT_PRICE},
        )
    if ceiling is None:
        raise CostRefused(
            "classifier_spend_ceiling",
            "CLASSIFIER_SPEND_CEILING_USD is not set, and paid classifier runs are refused "
            "until the operator sets the total they may spend.",
            {"setting": "CLASSIFIER_SPEND_CEILING_USD", "limit": None, "next": NEXT_CEILING},
        )
    if int(cost["rows_planned"]) > cap:
        raise CostRefused(
            "classifier_cap_exceeded",
            f"The run would score {cost['rows_planned']:,} rows on a paid endpoint; "
            f"CLASSIFIER_RUN_CAP_ROWS allows {cap:,} per run.",
            {
                "setting": "CLASSIFIER_RUN_CAP_ROWS",
                "limit": cap,
                "actual": int(cost["rows_planned"]),
                "next": NEXT_CAP,
            },
        )
    estimate = float(cost["estimated_usd"] or 0.0)
    if spent + estimate > ceiling:
        raise CostRefused(
            "classifier_spend_ceiling",
            f"Paid runs have spent ${spent:.6f}; this run's estimate of ${estimate:.6f} would "
            f"pass CLASSIFIER_SPEND_CEILING_USD (${ceiling:.6f}).",
            {
                "setting": "CLASSIFIER_SPEND_CEILING_USD",
                "limit": ceiling,
                "spent": round(spent, 6),
                "estimate": round(estimate, 6),
                "next": NEXT_CEILING,
            },
        )
    return facts


def run_spend(cost_basis: Mapping[str, Any] | None, scored_rows: int) -> float:
    """What one run spent: the rows it scored x its questions x its price (0 when unpaid)."""
    if not cost_basis or not cost_basis.get("paid"):
        return 0.0
    price = cost_basis.get("price_per_call_usd") or 0.0
    return float(Decimal(str(price)) * max(scored_rows, 0) * int(cost_basis.get("questions") or 1))
