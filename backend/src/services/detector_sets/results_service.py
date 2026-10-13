"""Reading probe evidence back from miStudio (FR-009.31 - FR-009.41, FR-009.70; FTDD 009 section 5.5).

The refresh is two halves so no blocking HTTP call runs on the event loop:

1. :func:`collect` (a worker thread): newest 200 runs (T-50), those whose ``train_dataset_id`` is
   this send's training view, their probes and each probe's report. A probe miStudio no longer has
   comes back as None.
2. :func:`store` (``run_sync``): map each report to figures through ``results_mapping`` by the
   probe dataset IDs this send recorded, compute a paired score only from an ACCEPTED source
   (``paired.accept_source``), move reward-marked probes out of every evaluation slot, compute
   ``gone`` against the previous snapshot, and insert the snapshot (insert-only).

miStudio unreachable during a refresh raises ``502`` and the previous snapshot stays as it was.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...clients import mistudio_client as mc
from ...core.canonical_json import canonical_sha256
from ...core.ids import new_id
from ...models.detector_results import DetectorResults
from ...models.detector_send import DetectorSend
from . import paired, results_mapping, reward_marks, send_service
from .errors import DetectorSetError
from .set_service import Who

RUN_SCAN_LIMIT = 200
NO_SOURCE_REASON = (
    "not available: miStudio serves no per-row scores, and no miLLM probe-verdict run over this "
    "role has reproduced miStudio's AUROC yet (T-47). Nothing is estimated."
)


@dataclass
class Collected:
    runs: list[dict[str, Any]] = field(default_factory=list)
    #: run id -> probe rows
    probes: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    #: probe id -> report, or None when miStudio no longer has the probe
    reports: dict[str, dict[str, Any] | None] = field(default_factory=dict)


@dataclass(frozen=True)
class SendView:
    send_id: str
    base_url: str
    train_view: str | None
    role_by_view: dict[str, dict[str, Any]]
    view_name_by_id: dict[str, str]
    pair_roles: dict[str, str]


def latest_completed_send(session: Session, set_id: str) -> DetectorSend:
    send = session.execute(
        select(DetectorSend)
        .where(DetectorSend.set_id == set_id, DetectorSend.state == "completed")
        .order_by(DetectorSend.completed_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if send is None:
        raise DetectorSetError(
            "send_not_found",
            "This set has no completed send, so miStudio holds none of its views yet. Send it first.",
        )
    return send


def send_view(session: Session, send: DetectorSend) -> SendView:
    steps = send_service.steps_of(session, send.id)
    roles = {r["id"]: r for r in send.snapshot["roles"]}
    by_view: dict[str, dict[str, Any]] = {}
    names: dict[str, str] = {}
    pairs: dict[str, str] = {}
    train_view = None
    for step in steps:
        if step.step != "register" or step.probe_dataset_id is None:
            continue
        role = roles[step.unit_key]
        by_view[step.probe_dataset_id] = {
            "role": role["role"],
            "role_id": role["id"],
            "version_id": role["version_id"],
            "split": role["split"],
            "input_column": role["input_column"],
            "label_column": role["label_column"],
            "label_mapping": role["label_mapping"],
            "pair_column": role["pair_column"],
        }
        names[step.probe_dataset_id] = role["view_name"]
        if role["pair_column"]:
            pairs[step.probe_dataset_id] = role["pair_column"]
        if role["role"] == "train":
            train_view = step.probe_dataset_id
    return SendView(send.id, send.mistudio_base_url, train_view, by_view, names, pairs)


def collect(client: mc.MiStudioClient, view: SendView) -> Collected:
    out = Collected()
    for run in client.list_runs(RUN_SCAN_LIMIT):
        if view.train_view is None or run.get("train_dataset_id") != view.train_view:
            continue
        out.runs.append(run)
        out.probes[str(run["id"])] = client.list_probes(str(run["id"]))
        for probe in out.probes[str(run["id"])]:
            out.reports[str(probe["id"])] = client.get_report(str(probe["id"]))
    return out


def _run_summary(run: Mapping[str, Any], view: SendView) -> dict[str, Any]:
    def role_of(view_id: str) -> str:
        found = view.role_by_view.get(view_id)
        return found["role"] if found else "not from this set"

    return {
        "id": run["id"],
        "model_id": run.get("model_id"),
        "status": run.get("status"),
        "train_dataset_id": run.get("train_dataset_id"),
        "eval_dataset_ids": [
            {"id": e, "role": role_of(str(e))} for e in run.get("eval_dataset_ids") or []
        ],
        "calibration_dataset_id": run.get("calibration_dataset_id"),
        "calibration_role": role_of(str(run.get("calibration_dataset_id"))),
        "config": run.get("config"),
        "model_dtype_label": (run.get("environment") or {}).get("model_dtype"),
        "created_at": run.get("created_at"),
    }


def _paired(session: Session, figure: dict[str, Any], view: SendView) -> None:
    """Attach a paired score, or its reason, to every set whose role has a pair column."""
    for entry in figure["sets"]:
        view_id = entry["probe_dataset_id"]
        if view_id not in view.pair_roles:
            continue
        source = reward_marks.millm_scores_for(
            session, figure["probe_id"], view.role_by_view[view_id]
        )
        if source is None:
            entry["paired"] = {"available": False, "reason": NO_SOURCE_REASON}
            continue
        scores, labels, groups = source
        ci = entry["ci"]
        accepted = paired.accept_source(
            "millm",
            scores,
            labels,
            reported_auroc=float(entry["auroc"]),
            reported_ci=(float(ci[0]), float(ci[1])) if ci else None,
        )
        if not accepted.accepted:
            entry["paired"] = {"available": False, "reason": accepted.reason}
            continue
        score = paired.paired_score(scores, labels, groups)
        entry["paired"] = (
            {"available": False, "reason": "no cross-label pair inside any group"}
            if score is None
            else {
                "available": True,
                "source": "millm",
                "acceptance": accepted.reason,
                "paired": score.paired,
                "ci": list(score.ci),
                "pairs": score.pairs,
                "groups": score.groups,
            }
        )


def _evaluation_slots(
    session: Session, view: SendView, collected: Collected, figures: list[dict[str, Any]]
) -> None:
    """FR-009.71: each reward probe lists who may evaluate it — a separate detector or a judge."""
    runs = {str(r["id"]): r for r in collected.runs}

    def traced(figure: dict[str, Any]) -> Any:
        run = runs.get(str(figure["run_id"]), {})
        return reward_marks.trace(
            session,
            base_url=view.base_url,
            probe_id=str(figure["probe_id"]),
            train_view_id=run.get("train_dataset_id"),
            model_id=run.get("model_id"),
            layer=figure.get("layer"),
        )

    for figure in figures:
        if not figure["reward"]:
            continue
        reward = traced(figure)
        figure["evaluation_slot"] = {
            "judge": reward_marks.evaluation_slot(reward, None),
            "detectors": [
                reward_marks.evaluation_slot(reward, traced(other))
                for other in figures
                if not other["reward"]
            ],
        }


def store(
    session: Session, set_id: str, view: SendView, collected: Collected, who: Who
) -> DetectorResults:
    marked = reward_marks.marked_probe_ids(session, view.base_url)
    figures: list[dict[str, Any]] = []
    report_sha: dict[str, str] = {}
    seen_runs = {str(r["id"]) for r in collected.runs}
    seen_probes: set[str] = set()
    for run_id, probes in collected.probes.items():
        for probe in probes:
            report = collected.reports.get(str(probe["id"]))
            if report is None:
                continue
            seen_probes.add(str(probe["id"]))
            report_sha[str(probe["id"])] = canonical_sha256(report)
            figure = results_mapping.figures_from_report(
                report, view.role_by_view, view.view_name_by_id
            )
            figure["run_id"] = run_id
            figure["reward"] = str(probe["id"]) in marked
            if figure["reward"]:
                # FR-009.70: a probe used as a training reward is never an evaluation.
                figure["group"] = "training reward - not an evaluation"
                figure["evaluation_slot"] = None
            else:
                figure["group"] = "evaluation"
                _paired(session, figure, view)
            figures.append(figure)
    _evaluation_slots(session, view, collected, figures)
    previous = session.execute(
        select(DetectorResults)
        .where(DetectorResults.set_id == set_id)
        .order_by(DetectorResults.read_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    gone: dict[str, Any] = {"runs": [], "probes": []}
    if previous is not None:
        for run in previous.runs:
            if str(run["id"]) not in seen_runs:
                gone["runs"].append({**run, "label": "no longer in miStudio"})
        for figure in previous.figures:
            if str(figure["probe_id"]) not in seen_probes and not figure.get("gone"):
                gone["probes"].append(figure["probe_id"])
                figures.append({**figure, "gone": True, "label": "no longer in miStudio"})
            elif figure.get("gone") and str(figure["probe_id"]) not in seen_probes:
                figures.append(figure)
    row = DetectorResults(
        id=new_id("dres"),
        set_id=set_id,
        send_id=view.send_id,
        mistudio_base_url=view.base_url,
        read_by=who.who,
        read_by_origin=who.origin,
        runs=[_run_summary(r, view) for r in collected.runs] + list(gone["runs"]),
        figures=figures,
        report_sha256=report_sha,
        gone=gone,
    )
    session.add(row)
    session.commit()
    return row


def results_out(row: DetectorResults) -> dict[str, Any]:
    figures = row.figures
    return {
        "id": row.id,
        "set_id": row.set_id,
        "send_id": row.send_id,
        "mistudio_base_url": row.mistudio_base_url,
        "read_at": row.read_at.isoformat() if row.read_at else None,
        "read_by": row.read_by,
        "read_by_origin": row.read_by_origin,
        "runs": row.runs,
        "evaluations": [f for f in figures if not f.get("reward")],
        "training_reward": [f for f in figures if f.get("reward")],
        "gone": row.gone,
        "report_sha256": row.report_sha256,
    }


def snapshot(session: Session, set_id: str, which: str) -> DetectorResults:
    query = select(DetectorResults).where(DetectorResults.set_id == set_id)
    if which != "latest":
        query = query.where(DetectorResults.id == which)
    row = session.execute(
        query.order_by(DetectorResults.read_at.desc()).limit(1)
    ).scalar_one_or_none()
    if row is None:
        raise DetectorSetError(
            "report_not_found", "No results snapshot yet; press Refresh results after a run."
        )
    return row


def list_snapshots(session: Session, set_id: str) -> Sequence[DetectorResults]:
    return list(
        session.execute(
            select(DetectorResults)
            .where(DetectorResults.set_id == set_id)
            .order_by(DetectorResults.read_at.desc())
        ).scalars()
    )
