"""Labeler identities of a version's bound label runs (008 M-7 and C-4; 008's seam
``feature_seams.labelers_for`` imports this module by the path its FTDD names).

Filled by feature 006 (2026-10-07): 008 left the seam reading "not built", so check C-6 stayed
amber whatever a calibration record said, and 006's acceptance (a valid record lets 008's checks
pass) could not be met. Every value comes from a real ``dw_label_runs`` / ``dw_decision_templates``
column; a fact the run does not record is ``None`` (``model_revision`` "not reported" stays as 005
wrote it), never a plausible default. One entry per labeler FINGERPRINT, with every run that
produced it, in the contract's ``Labeler`` shape.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models.decision_template import DecisionTemplate
from ...models.label_run import LabelRun
from ...models.rubric import Rubric

_ROLES = {"classifier": "classifier", "judge": "judge"}


def _role(run: LabelRun) -> str:
    if run.kind in _ROLES:
        return _ROLES[run.kind]
    snapshot_role = (run.endpoint_snapshot or {}).get("role")
    return "classifier" if snapshot_role == "classifier" else "judge"


def _excluded_share(run: LabelRun) -> float | None:
    counts = run.counts or {}
    total = sum(int(v) for v in counts.values())
    if not total or "excluded" not in counts:
        return None
    return int(counts["excluded"]) / total


def labelers_for_runs(run_ids: Sequence[str], *, session: Session) -> list[dict[str, Any]]:
    runs = list(
        session.execute(
            select(LabelRun).where(LabelRun.id.in_(list(run_ids))).order_by(LabelRun.created_at)
        ).scalars()
    )
    by_fingerprint: dict[str, dict[str, Any]] = {}
    for run in runs:
        entry = by_fingerprint.get(run.labeler_fingerprint)
        if entry is not None:
            entry["run_ids"].append(run.id)
            continue
        identity = run.labeler_identity or {}
        template = session.get(DecisionTemplate, run.template_id) if run.template_id else None
        rubric = session.get(Rubric, run.rubric_id) if run.rubric_id else None
        revision = identity.get("model_revision")
        by_fingerprint[run.labeler_fingerprint] = {
            "fingerprint": run.labeler_fingerprint,
            "role": _role(run),
            "protocol": (run.endpoint_snapshot or {}).get("protocol"),
            "model_id": (run.endpoint_snapshot or {}).get("model_id") or identity.get("model_id"),
            "model_revision": revision if isinstance(revision, str) else None,
            "revision_reported": bool(run.revision_reported),
            "pinned": bool(run.pinned),
            "template": (
                {
                    "name": template.name,
                    "version": str(template.version),
                    "content_sha256": template.content_hash,
                }
                if template is not None
                else None
            ),
            "question": run.question,
            "label_set": (
                list(run.label_set)
                if run.label_set
                else [x for x in (run.positive_label, run.negative_label) if x]
            ),
            "thresholds": (
                {
                    "positive_at_or_above": run.threshold_positive,
                    "negative_at_or_below": run.threshold_negative,
                }
                if run.threshold_positive is not None or run.threshold_negative is not None
                else None
            ),
            "excluded_share": _excluded_share(run),
            "run_ids": [run.id],
            # A judge's rubric (M-7 "template or rubric"): the contract's ``template`` is a
            # DECISION template, so the rubric rides in the labeler's extensions.
            "extensions": (
                {
                    "rubric": {
                        "name": rubric.name,
                        "version": str(rubric.version),
                        "content_sha256": rubric.content_hash,
                    }
                }
                if rubric is not None
                else {}
            ),
        }
    return list(by_fingerprint.values())
