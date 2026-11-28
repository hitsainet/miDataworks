"""The suite result language's approval object, computed from a stored approval (022 FR-022.3 - .5).

``mistudio.result-language/v1`` (owned by the suite's front-door app, vendored at
``docs/schemas/mistudio-result-language-v1.json`` and read by tests only) gives every app one
``approval`` shape. miDataworks adds it beside its own pending fields and approval reads; nothing is
stored. miDataworks is server-executed (suite CHAIN_PLAN A18): it runs an approved request itself,
so ``then`` is always ``server_runs``, ``call_again_with`` is never set, and ``approved`` is never
emitted (there is no state between the decision and the run).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

#: Every status the approval service writes, mapped to the contract's word: each is its own
#: (A6 as amended 2026-11-15 holds ``executing`` and ``failed``).
STATUS_MAP: dict[str, str] = {
    s: s for s in ("pending", "executing", "executed", "failed", "rejected", "expired")
}

DECIDE_IN = "midataworks"
#: The contract's ``approval.summary`` bounds.
SUMMARY_MAX = 500


class ApprovalLike(Protocol):
    id: str
    status: str
    expires_at: datetime
    summary: str
    action: str


def approval_view(row: ApprovalLike, now: datetime) -> dict[str, Any]:
    """The contract's ``approval`` object for one stored request.

    A pending request past its expiry that the hourly sweeper has not marked yet reads
    ``expired`` (022-D9): the shared field never says pending for a request that can no longer
    run. The legacy ``status`` keeps the row's value.
    """
    status = row.status
    if status == "pending" and row.expires_at <= now:
        status = "expired"
    summary = (row.summary or "").strip() or row.action
    if len(summary) > SUMMARY_MAX:
        summary = summary[: SUMMARY_MAX - 1].rstrip() + "…"
    return {
        "id": row.id,
        "status": STATUS_MAP[status],
        "decide_in": DECIDE_IN,
        "then": "server_runs",
        "summary": summary,
    }
