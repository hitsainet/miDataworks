"""Warning levels: one global default, overridable per dataset (FR-004.33; C4, P-19, P-09).

The current level of a scope is its latest ``dw_shortcut_levels`` row. The effective level of a
dataset is its override when the latest dataset row is a ``set``, else the latest global ``set``,
else the code default :data:`~.shortcut_rules.DEFAULT_SHORTCUT_MARGIN_PP` (P-19). Every answer
says where it came from, so a warning can name the level in force (FR-004.34).

Writes are the operator's only: the routes refuse agents with 403 before calling here, and the
table's ``origin = 'operator'`` check is the second wall. This module takes ``set_by`` from the
caller (the Settings operator name, C5) and never reads a "who" from a request body.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models.curation import LevelAction, LevelScope, ShortcutLevel
from ...models.dataset import Dataset
from .errors import CurationError
from .shortcut_rules import DEFAULT_SHORTCUT_MARGIN_PP

HISTORY_LIMIT = 50


@dataclass(frozen=True)
class EffectiveLevel:
    margin_pp: float
    #: ``dataset`` (an override), ``global`` (a set global level) or ``code_default`` (P-19).
    source: str
    set_by: str | None
    set_at: datetime | None
    reason: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "margin_pp": self.margin_pp,
            "source": self.source,
            "set_by": self.set_by,
            "set_at": self.set_at.isoformat() if self.set_at else None,
            "reason": self.reason,
        }


def _latest(session: Session, scope: str, dataset_id: str | None) -> ShortcutLevel | None:
    query = select(ShortcutLevel).where(ShortcutLevel.scope == scope)
    query = (
        query.where(ShortcutLevel.dataset_id.is_(None))
        if dataset_id is None
        else query.where(ShortcutLevel.dataset_id == dataset_id)
    )
    return session.execute(
        query.order_by(ShortcutLevel.created_at.desc(), ShortcutLevel.id.desc()).limit(1)
    ).scalar_one_or_none()


def global_level(session: Session) -> EffectiveLevel:
    row = _latest(session, LevelScope.GLOBAL, None)
    if row is None or row.margin_pp is None:
        return EffectiveLevel(float(DEFAULT_SHORTCUT_MARGIN_PP), "code_default", None, None, None)
    return EffectiveLevel(float(row.margin_pp), "global", row.set_by, row.created_at, row.reason)


def effective_level(session: Session, dataset_id: str | None) -> EffectiveLevel:
    """The dataset's override if one is in force, else the global level (FR-004.33)."""
    if dataset_id is not None:
        row = _latest(session, LevelScope.DATASET, dataset_id)
        if row is not None and row.action == LevelAction.SET and row.margin_pp is not None:
            return EffectiveLevel(
                float(row.margin_pp), "dataset", row.set_by, row.created_at, row.reason
            )
    return global_level(session)


def history(session: Session, dataset_id: str | None) -> list[dict[str, Any]]:
    scope = LevelScope.GLOBAL if dataset_id is None else LevelScope.DATASET
    query = select(ShortcutLevel).where(ShortcutLevel.scope == scope)
    query = (
        query.where(ShortcutLevel.dataset_id.is_(None))
        if dataset_id is None
        else query.where(ShortcutLevel.dataset_id == dataset_id)
    )
    rows = session.execute(
        query.order_by(ShortcutLevel.created_at.desc(), ShortcutLevel.id.desc()).limit(
            HISTORY_LIMIT
        )
    ).scalars()
    return [
        {
            "action": r.action,
            "margin_pp": float(r.margin_pp) if r.margin_pp is not None else None,
            "reason": r.reason,
            "set_by": r.set_by,
            "origin": r.origin,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


def _check(margin_pp: float | None, reason: str | None) -> tuple[Decimal | None, str]:
    text = (reason or "").strip()
    if not text:
        raise CurationError(
            "override_reason_required",
            "Give a reason for changing the warning level; it is kept in the level's history.",
        )
    if margin_pp is None:
        return None, text
    if not (0 <= float(margin_pp) < 100):
        raise CurationError(
            "margin_out_of_range",
            "The warning level is a margin in percentage points above chance: 0 or more and "
            "below 100.",
            {"margin_pp": margin_pp},
        )
    return Decimal(str(round(float(margin_pp), 2))), text


def require_dataset(session: Session, dataset_id: str) -> Dataset:
    import uuid

    try:
        key = str(uuid.UUID(str(dataset_id)))
    except ValueError:
        key = None
    dataset = session.get(Dataset, key) if key else None
    if dataset is None:
        raise CurationError("dataset_not_found", f"No dataset {dataset_id}.")
    return dataset


def set_level(
    session: Session,
    *,
    dataset_id: str | None,
    margin_pp: float,
    reason: str,
    set_by: str,
    origin: str = "operator",
) -> ShortcutLevel:
    """Record a ``set`` for the dataset (an override) or the global level."""
    margin, text = _check(margin_pp, reason)
    if dataset_id is not None:
        require_dataset(session, dataset_id)
    row = ShortcutLevel(
        scope=LevelScope.GLOBAL if dataset_id is None else LevelScope.DATASET,
        dataset_id=dataset_id,
        action=LevelAction.SET,
        margin_pp=margin,
        reason=text,
        set_by=set_by,
        origin=origin,  # the caller's REAL origin: the table check is the second wall
    )
    session.add(row)
    session.commit()
    return row


def clear_level(
    session: Session, *, dataset_id: str, reason: str, set_by: str, origin: str = "operator"
) -> ShortcutLevel:
    """Record a ``clear`` of a dataset's override: the global level applies again."""
    _, text = _check(None, reason)
    require_dataset(session, dataset_id)
    row = ShortcutLevel(
        scope=LevelScope.DATASET,
        dataset_id=dataset_id,
        action=LevelAction.CLEAR,
        margin_pp=None,
        reason=text,
        set_by=set_by,
        origin=origin,  # the caller's REAL origin: the table check is the second wall
    )
    session.add(row)
    session.commit()
    return row
