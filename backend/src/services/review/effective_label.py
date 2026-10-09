"""The ONE effective-label resolver, ``dw.effective-label/v1`` (FR-006.25; FTDD 006 section 6.4).

``resolve(version_id, question_hash, label_run_id, row_keys)`` returns ``{row_key: EffectiveState}``
for every requested key. Feature 008's projection calls it in every export (through
``services/publishing/feature_seams.resolve_effective_labels``): an ``overridden`` row takes the
operator's label, a ``flagged_unresolved`` row is omitted, a ``model`` row keeps its label. The
fold is :func:`decision_rules.effective_state` — only an OPERATOR override wins (P-10).

Which decisions count: those on the requested row keys for the question. Decisions are keyed by
row key and question (FR-002.40), so they survive rebuilds that leave a row unchanged. With
``question_hash=None`` (008 does not know the question) the questions are those of the version's
bound label runs; a version with no bound run takes the decisions made in that version.

One SQL query per call (the decision index ``(row_key, question_hash, created_at)``), keys sent
in chunks. The model label is read from ``label_run_id``'s labels when given.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.database import get_sync_db
from ...models.label import Label
from ...models.label_run import LabelRun
from ...models.review import ReviewDecision
from ...models.version import Version
from ..calibration.constants import RESOLVER_ID
from ..calibration.mapping import question_hash as hash_question
from .decision_rules import EffectiveState, effective_state

__all__ = ["RESOLVER_ID", "EffectiveState", "resolve", "resolve_with"]

_CHUNK = 5000


def _questions(session: Session, version: Version | None) -> list[str]:
    if version is None:
        return []
    run_ids = [str(b["id"]) for b in version.bindings if b.get("kind") == "label_run"]
    if not run_ids:
        return []
    questions = session.execute(
        select(LabelRun.question).where(LabelRun.id.in_(run_ids), LabelRun.question.is_not(None))
    ).scalars()
    return sorted({hash_question(q) for q in questions if q})


def resolve_with(
    session: Session,
    version_id: str,
    question_hash: str | None,
    label_run_id: str | None,
    row_keys: Sequence[str],
) -> dict[str, EffectiveState]:
    keys = list(dict.fromkeys(row_keys))
    model: dict[str, str | None] = {}
    if label_run_id is not None:
        run = session.get(LabelRun, label_run_id)
        if run is not None:
            from .queue_service import label_name, run_label_set

            labels = run_label_set(run)
            for i in range(0, len(keys), _CHUNK):
                chunk = keys[i : i + _CHUNK]
                for row_key, outcome in session.execute(
                    select(Label.row_key, Label.outcome).where(
                        Label.label_run_id == label_run_id, Label.row_key.in_(chunk)
                    )
                ).all():
                    model[row_key] = label_name(outcome, labels)
    if question_hash is not None:
        hashes: list[str] | None = [question_hash]
    else:
        hashes = _questions(session, session.get(Version, version_id)) or None
    history: dict[str, list[ReviewDecision]] = {}
    for i in range(0, len(keys), _CHUNK):
        chunk = keys[i : i + _CHUNK]
        query = select(ReviewDecision).where(ReviewDecision.row_key.in_(chunk))
        if hashes is not None:
            query = query.where(ReviewDecision.question_hash.in_(hashes))
        else:
            query = query.where(ReviewDecision.version_id == version_id)
        for d in session.execute(
            query.order_by(ReviewDecision.created_at, ReviewDecision.id)
        ).scalars():
            history.setdefault(str(d.row_key), []).append(d)
    return {k: effective_state(history.get(k, []), model.get(k)) for k in keys}


def resolve(
    version_id: str,
    question_hash: str | None,
    label_run_id: str | None,
    row_keys: Sequence[str],
) -> dict[str, EffectiveState]:
    """The effective label of each row key (008 calls this function object)."""
    with get_sync_db() as session:
        return resolve_with(session, version_id, question_hash, label_run_id, row_keys)
