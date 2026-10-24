"""Reward marks, the evaluation-slot rule and the miLLM per-row score source (FR-009.38,
FR-009.69 - FR-009.71, FR-009.79; P-21; R-03.53).

- A mark is insert-only and has no unmark route (TQ9): a probe once used as a training reward stays
  one. ``mark`` refuses a second mark of the same probe with ``409 already_marked``.
- :func:`marked_probe_ids` is what results, cards and MCP results read to keep a marked probe OUT
  of every evaluation slot (FR-009.70).
- :func:`evaluation_slot` says who may evaluate a reward-trained model: a SEPARATE detector
  (``separation.separate``: different probe, disjoint training rows traced through 009's
  registrations) or a judge. A probe whose rows cannot be traced is refused, never assumed separate.
- :func:`millm_scores_for` returns the per-row scores of a completed ``probe_verdict`` label run of
  this probe over a role's rows (M4), aligned with the role's labels and pair groups, for
  ``paired.accept_source``. None when no such run exists. The run is found by the identity's
  ``mistudio_probe_id`` (009's probe protocol), since the figures name miStudio's probe.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...core.ids import new_id
from ...models.detector_results import RewardMark
from ...models.detector_send import MiStudioRegistration
from ...models.label import Label
from ...models.label_run import LabelRun
from ...models.version import Version
from . import separation
from .errors import DetectorSetError
from .label_rules import value_key
from .set_service import Who, split_path


def mark(
    session: Session,
    *,
    base_url: str,
    probe_id: str,
    reason: str,
    who: Who,
    source: str,
    export_id: str | None = None,
) -> RewardMark:
    row = RewardMark(
        id=new_id("rwm"),
        mistudio_base_url=base_url,
        mistudio_probe_id=probe_id,
        source=source,
        export_id=export_id,
        reason=reason,
        marked_by=who.who,
        marked_by_origin=who.origin,
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise DetectorSetError(
            "already_marked",
            f"Probe {probe_id} is already marked as a training reward; a mark is never removed.",
            {"probe_id": probe_id},
        ) from None
    return row


def mark_from_export(
    session: Session, *, base_url: str, export_id: str, probe_ids: list[str], who: Who
) -> list[RewardMark]:
    """008's hook for an export that names probe IDs (FR-009.69). Already-marked probes are kept."""
    out: list[RewardMark] = []
    for probe_id in sorted(set(probe_ids)):
        if probe_id in marked_probe_ids(session, base_url):
            continue
        out.append(
            mark(
                session,
                base_url=base_url,
                probe_id=probe_id,
                reason=f"used as a training reward by export {export_id}",
                who=who,
                source="export",
                export_id=export_id,
            )
        )
    return out


def probes_trained_on(session: Session, version: Version) -> list[str]:
    """The miStudio probe IDs whose verdicts this version's rows carry: every ``probe_verdict``
    label run its build bound, by the identity's ``mistudio_probe_id`` (009's probe protocol).
    An export of such a version trains on the probe's verdicts, so the probe is a training
    signal from then on (FR-009.69; suite tracker 4.6, interpretation recorded for the operator).
    A run whose identity reports no miStudio probe ID cannot be marked and is left out."""
    from .probe_protocol import NOT_REPORTED

    run_ids = [str(b["id"]) for b in version.bindings if b.get("kind") == "label_run"]
    if not run_ids:
        return []
    runs = session.execute(
        select(LabelRun).where(LabelRun.id.in_(run_ids), LabelRun.kind == "probe_verdict")
    ).scalars()
    ids = {
        str(pid)
        for run in runs
        if (pid := (run.labeler_identity or {}).get("mistudio_probe_id")) and pid != NOT_REPORTED
    }
    return sorted(ids)


def mark_export(session: Session, export: Any, version: Version) -> dict[str, Any]:
    """008's hook (009 FTASKS 8.2): called by every completed export. Marks each probe the
    version trained on, naming the export; already-marked probes are kept. Without miStudio
    configured there is nothing to mark against (a mark names a miStudio): the probes are
    returned as ``unmarked`` with the reason, never silently dropped."""
    from ...core.config import get_settings

    probes = probes_trained_on(session, version)
    if not probes:
        return {"probes": [], "marked": []}
    base_url = get_settings().mistudio_base_url
    if not base_url:
        return {
            "probes": probes,
            "marked": [],
            "unmarked_reason": "MISTUDIO_BASE_URL is not configured; mark these probes by hand "
            "once it is (POST /api/v1/reward-marks).",
        }
    made = mark_from_export(
        session,
        base_url=base_url,
        export_id=export.id,
        probe_ids=probes,
        who=Who(export.started_by, export.started_by_origin),
    )
    return {"probes": probes, "marked": [m.mistudio_probe_id for m in made]}


def marked_probe_ids(session: Session, base_url: str) -> set[str]:
    return set(
        session.execute(
            select(RewardMark.mistudio_probe_id).where(RewardMark.mistudio_base_url == base_url)
        ).scalars()
    )


def list_marks(session: Session) -> list[RewardMark]:
    return list(session.execute(select(RewardMark).order_by(RewardMark.marked_at.desc())).scalars())


def mark_out(row: RewardMark) -> dict[str, Any]:
    return {
        "id": row.id,
        "mistudio_base_url": row.mistudio_base_url,
        "mistudio_probe_id": row.mistudio_probe_id,
        "source": row.source,
        "export_id": row.export_id,
        "reason": row.reason,
        "marked_by": row.marked_by,
        "marked_by_origin": row.marked_by_origin,
        "marked_at": row.marked_at.isoformat() if row.marked_at else None,
    }


# --- P-21 tracing -----------------------------------------------------------------------------


def trace(
    session: Session,
    *,
    base_url: str,
    probe_id: str,
    train_view_id: str | None,
    model_id: str | None,
    layer: int | None,
) -> separation.ProbeTrace:
    """probe -> run's training view -> 009's registration -> version and split -> row keys."""
    if train_view_id is None:
        return separation.ProbeTrace(probe_id, model_id, layer, None, "no training view recorded")
    reg = session.execute(
        select(MiStudioRegistration).where(
            MiStudioRegistration.kind == "view",
            MiStudioRegistration.mistudio_base_url == base_url,
            MiStudioRegistration.probe_dataset_id == train_view_id,
        )
    ).scalar_one_or_none()
    if reg is None or reg.split is None:
        return separation.ProbeTrace(
            probe_id, model_id, layer, None, f"no send recorded the training view {train_view_id}"
        )
    version = session.get(Version, reg.version_id)
    if version is None or version.state != "completed":
        return separation.ProbeTrace(
            probe_id, model_id, layer, None, "the training version's rows are gone"
        )
    keys = pq.read_table(split_path(version, reg.split), columns=["_dw_row_key"]).column(0)
    return separation.ProbeTrace(probe_id, model_id, layer, set(keys.to_pylist()))


def evaluation_slot(
    reward: separation.ProbeTrace, evaluator: separation.ProbeTrace | None
) -> dict[str, Any]:
    """FR-009.71: a reward-trained model is evaluated by a SEPARATE detector or a judge."""
    if evaluator is None:
        return {"evaluator": "judge", "allowed": True, "reason": "a judge is not the reward probe"}
    result = separation.separate(reward, evaluator)
    return {
        "evaluator": evaluator.probe_id,
        "allowed": result.verdict == "separate",
        "verdict": result.verdict,
        "reason": result.reason,
        "warnings": result.warnings,
    }


# --- the miLLM per-row score source (M4) ------------------------------------------------------


def millm_scores_for(
    session: Session, probe_id: str, role: Mapping[str, Any]
) -> tuple[list[float], list[bool], list[Any]] | None:
    """Scores of a completed probe-verdict run of ``probe_id`` over the role's split, in row order,
    with labels (positive rows True) and pair groups. Excluded-mapped rows are left out."""
    run = session.execute(
        select(LabelRun)
        .where(
            LabelRun.kind == "probe_verdict",
            LabelRun.state == "completed",
            LabelRun.input_version_id == role["version_id"],
            # The SOURCE miStudio probe: ``probe_id`` in a probe-verdict identity is miLLM's own ID
            # (``pr_...``), and the figures here are keyed by miStudio's (``pm_...``).
            LabelRun.labeler_identity["mistudio_probe_id"].astext == probe_id,
        )
        .order_by(LabelRun.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if run is None:
        return None
    version = session.get(Version, role["version_id"])
    assert version is not None
    columns = ["_dw_row_key", role["label_column"], role["pair_column"]]
    table = pq.read_table(Path(split_path(version, role["split"])), columns=columns)
    scores_by_key: dict[str, float] = {}
    for label in session.execute(select(Label).where(Label.label_run_id == run.id)).scalars():
        value = label.parsed_value
        if isinstance(value, dict) and value.get("score") is not None:
            scores_by_key[label.row_key] = float(value["score"])
    scores: list[float] = []
    labels: list[bool] = []
    groups: list[Any] = []
    mapping = {value_key(k): v for k, v in dict(role["label_mapping"]).items()}
    for key, raw, group in zip(
        table.column(0).to_pylist(),
        table.column(1).to_pylist(),
        table.column(2).to_pylist(),
        strict=True,
    ):
        target = mapping.get(value_key(raw)) if raw is not None else None
        if target not in ("positive", "negative"):
            continue
        if key not in scores_by_key:
            return None  # a row the run did not score: the array would not reproduce the set
        scores.append(scores_by_key[key])
        labels.append(target == "positive")
        groups.append(group)
    return scores, labels, groups
