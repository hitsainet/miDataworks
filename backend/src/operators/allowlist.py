"""The entry-point allowlist: read current state, write allow and revoke (FR-003.11; FTID 003 section 4).

State is read from PostgreSQL every time it is checked, never cached (FTDD 003 section 7.1): an
allowlist change takes effect for the next request in the API and the next step in a worker.
Writes take ``changed_by`` from the caller (the route passes the Settings operator name, C5) and a
non-empty reason; ``origin`` is always ``'operator'`` and the table refuses anything else (P-09).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..core.database import get_sync_db
from ..models.operator_allowlist import OperatorAllowlistEntry
from .errors import OperatorError

Triple = tuple[str, str, str]


@dataclass(frozen=True)
class HistoryRow:
    distribution: str
    distribution_version: str
    entry_point: str
    action: str
    reason: str
    changed_by: str
    created_at: datetime


_CURRENT = text(
    "SELECT DISTINCT ON (distribution, distribution_version, entry_point) "
    "distribution, distribution_version, entry_point, action "
    "FROM dw_operator_allowlist "
    "ORDER BY distribution, distribution_version, entry_point, created_at DESC, id DESC"
)


def allowed_triples(session: Session) -> set[Triple]:
    """Every triple whose latest decision is ``allow``."""
    rows = session.execute(_CURRENT).all()
    return {(r[0], r[1], r[2]) for r in rows if r[3] == "allow"}


def read_allowed() -> set[Triple]:
    """The current allowed set, read through a fresh sync session (API and workers alike)."""
    with get_sync_db() as session:
        return allowed_triples(session)


def history(session: Session) -> list[HistoryRow]:
    rows = session.query(OperatorAllowlistEntry).order_by(
        OperatorAllowlistEntry.created_at.desc(), OperatorAllowlistEntry.id.desc()
    )
    return [
        HistoryRow(
            r.distribution,
            r.distribution_version,
            r.entry_point,
            r.action,
            r.reason,
            r.changed_by,
            r.created_at,
        )
        for r in rows
    ]


def _write(session: Session, triple: Triple, action: str, reason: str, changed_by: str) -> None:
    if not reason or not reason.strip():
        raise OperatorError(
            "reason_required",
            f"Say why you {action} this entry point; the reason is kept in its history.",
        )
    if not changed_by or not changed_by.strip():
        raise OperatorError(
            "reason_required", "Set your name in Settings; it is recorded as who changed this."
        )
    session.add(
        OperatorAllowlistEntry(
            distribution=triple[0],
            distribution_version=triple[1],
            entry_point=triple[2],
            action=action,
            reason=reason.strip(),
            changed_by=changed_by.strip(),
            origin="operator",
        )
    )
    session.commit()


def allow(session: Session, triple: Triple, reason: str, changed_by: str) -> None:
    _write(session, triple, "allow", reason, changed_by)


def revoke(session: Session, triple: Triple, reason: str, changed_by: str) -> None:
    _write(session, triple, "revoke", reason, changed_by)


#: The reader the registry uses; tests may install a fake (no database).
AllowedReader = Callable[[], set[Triple]]
