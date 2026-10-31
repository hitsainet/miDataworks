"""Reading probe evidence back from miStudio (FR-009.31 - FR-009.41, FR-009.70; FTDD 009 section 5.5).

The refresh is two halves so no blocking HTTP call runs on the event loop:

1. :func:`collect` (a worker thread): the runs trained on this send's training view — asked of
   miStudio by ``train_dataset_id`` when it serves the filter (011 XR-4, ``runs_train_filter``),
   else found by scanning the newest 200 runs (T-50), recorded in ``run_discovery`` with the note
   "older runs may be missing"; their probes and each probe's report (a probe miStudio no longer
   has comes back as None); and, when miStudio serves per-row scores (011 XR-2), every page of the
   scores of each evaluation of a pair-column view.
2. :func:`store` (``run_sync``): map each report to figures through ``results_mapping`` by the
   probe dataset IDs this send recorded, compute a paired score only from an ACCEPTED source, in
   order miStudio's arrays (5e-4) then miLLM probe-verdict scores (011 D19), naming the source used
   and why each other was skipped; move reward-marked probes out of every evaluation slot, compute
   ``gone`` against the previous snapshot, record each role's commit and ``commit_check``, and
   insert the snapshot (insert-only).

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
from . import (
    capabilities,
    commit_check,
    mistudio_scores,
    paired,
    results_mapping,
    reward_marks,
    send_service,
)
from .errors import DetectorSetError
from .set_service import Who

RUN_SCAN_LIMIT = 200
SCAN_NOTE = "scanned the newest 200 runs; older runs may be missing"
NO_SOURCE_REASON = (
    "not available: neither miStudio's per-row scores nor a miLLM probe-verdict run over this "
    "role reproduced miStudio's AUROC (T-47). Nothing is estimated."
)
#: 011 D19: miStudio's arrays are the evaluation that produced the AUROC; miLLM re-scores rows.
SOURCE_ORDER: tuple[str, ...] = ("mistudio", "millm")
NO_MILLM_RUN = "no completed miLLM probe-verdict run over this role's version for this probe (T-47)"


@dataclass
class Collected:
    runs: list[dict[str, Any]] = field(default_factory=list)
    #: run id -> probe rows
    probes: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    #: probe id -> report, or None when miStudio no longer has the probe
    reports: dict[str, dict[str, Any] | None] = field(default_factory=dict)
    #: 011 FR-011.11: how the runs were found.
    run_discovery: dict[str, Any] = field(default_factory=dict)
    #: 011 XR-2: whether miStudio served per-row scores, and why not.
    per_row_scores: bool = False
    per_row_reason: str = ""
    #: (probe id, evaluation id) -> all pages of the scores, or miStudio's refusal.
    scores: dict[tuple[str, str], dict[str, Any] | mistudio_scores.Refusal] = field(
        default_factory=dict
    )


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


def collect(
    client: mc.MiStudioClient, view: SendView, caps: capabilities.Capabilities | None = None
) -> Collected:
    caps = caps if caps is not None else capabilities.read(client)
    out = Collected()
    if caps.runs_train_filter and view.train_view is not None:
        # 011 FR-011.10: miStudio finds them, however old; no scan.
        listed = client.list_runs(RUN_SCAN_LIMIT, train_dataset_id=view.train_view)
        out.run_discovery = {"method": "filter", "limit": RUN_SCAN_LIMIT}
    else:
        listed = client.list_runs(RUN_SCAN_LIMIT)
        out.run_discovery = {"method": "scan", "limit": RUN_SCAN_LIMIT, "note": SCAN_NOTE}
    out.per_row_scores = caps.per_row_scores
    out.per_row_reason = caps.as_dict()["reasons"]["per_row_scores"]
    for run in listed:
        # Kept with the filter too: a miStudio that ignored the parameter must not put another
        # set's runs in this set's snapshot.
        if view.train_view is None or run.get("train_dataset_id") != view.train_view:
            continue
        out.runs.append(run)
        out.probes[str(run["id"])] = client.list_probes(str(run["id"]))
        for probe in out.probes[str(run["id"])]:
            report = client.get_report(str(probe["id"]))
            out.reports[str(probe["id"])] = report
            if report is not None and caps.per_row_scores:
                for evaluation in report["evaluations"]:
                    if str(evaluation["dataset_id"]) in view.pair_roles:
                        key = (str(probe["id"]), str(evaluation["id"]))
                        out.scores[key] = mistudio_scores.fetch(client, *key)
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


def _paired_out(
    source: str, accepted: paired.Acceptance, scores: Any, labels: Any, groups: Any
) -> dict[str, Any]:
    score = paired.paired_score(scores, labels, groups)
    if score is None:
        return {
            "available": False,
            "source": source,
            "reason": "no cross-label pair inside any group",
        }
    return {
        "available": True,
        "source": source,
        "acceptance": accepted.reason,
        "paired": score.paired,
        "ci": list(score.ci),
        "pairs": score.pairs,
        "groups": score.groups,
    }


def _mistudio_source(
    session: Session,
    figure: dict[str, Any],
    entry: dict[str, Any],
    view: SendView,
    collected: Collected,
) -> tuple[dict[str, Any] | None, str]:
    """The paired score from miStudio's own arrays (011 FR-011.13 - FR-011.18), or the reason it
    was skipped. Accepted only within 5e-4 of the AUROC miStudio reports for that evaluation."""
    if not collected.per_row_scores:
        return None, collected.per_row_reason
    view_id = entry["probe_dataset_id"]
    key = (str(figure["probe_id"]), str(entry["evaluation_id"]))
    served = collected.scores.get(key)
    if served is None:
        return None, f"no scores were read for evaluation {key[1]}"
    if isinstance(served, mistudio_scores.Refusal):
        return None, served.reason
    joined = mistudio_scores.join(
        session, view.send_id, view_id, view.role_by_view[view_id], served
    )
    if isinstance(joined, mistudio_scores.Refusal):
        return None, joined.reason
    if joined.reported_auroc is None:
        return None, f"miStudio reports no AUROC for evaluation {key[1]} (a refused evaluation)"
    accepted = paired.accept_source(
        "mistudio",
        joined.scores,
        joined.labels,
        reported_auroc=joined.reported_auroc,
        reported_ci=None,
    )
    if not accepted.accepted:
        return None, accepted.reason
    return _paired_out("mistudio", accepted, joined.scores, joined.labels, joined.groups), ""


def _millm_source(
    session: Session, figure: dict[str, Any], entry: dict[str, Any], view: SendView
) -> tuple[dict[str, Any] | None, str]:
    """The paired score from a miLLM probe-verdict run over the role (M4, T-47), judged against
    miStudio's reported interval, or the reason it was skipped."""
    source = reward_marks.millm_scores_for(
        session, figure["probe_id"], view.role_by_view[entry["probe_dataset_id"]]
    )
    if source is None:
        return None, NO_MILLM_RUN
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
        return None, accepted.reason
    return _paired_out("millm", accepted, scores, labels, groups), ""


def _paired(session: Session, figure: dict[str, Any], view: SendView, collected: Collected) -> None:
    """Attach a paired score, or its reason, to every set whose role has a pair column. Sources in
    order (011 D19): miStudio's arrays, then miLLM's scores; ``source`` names the one used and
    ``skipped`` why each earlier one was not."""
    for entry in figure["sets"]:
        if entry["probe_dataset_id"] not in view.pair_roles:
            continue
        skipped: list[dict[str, str]] = []
        result: dict[str, Any] | None = None
        for name in SOURCE_ORDER:
            if name == "mistudio":
                result, reason = _mistudio_source(session, figure, entry, view, collected)
            else:
                result, reason = _millm_source(session, figure, entry, view)
            if result is not None:
                break
            skipped.append({"source": name, "reason": reason})
        if result is None:
            rejected = [s["reason"] for s in skipped if s["reason"].startswith("AUROC")]
            result = {
                "available": False,
                "source": None,
                "reason": rejected[-1] if rejected else NO_SOURCE_REASON,
            }
        entry["paired"] = {**result, "skipped": skipped}


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
                _paired(session, figure, view, collected)
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
        provenance={
            "run_discovery": collected.run_discovery,
            "commits": role_commits(session, view),
        },
    )
    session.add(row)
    session.commit()
    return row


def role_commits(session: Session, view: SendView) -> list[dict[str, Any]]:
    """Each registered role's published commit and whether miStudio confirmed it (011 FR-011.8,
    FTID I3), copied from the send's download steps: a completed send never changes."""
    send = session.get(DetectorSend, view.send_id)
    assert send is not None
    units = {u["key"]: u for u in send.plan["download_units"]}
    steps = [s for s in send_service.steps_of(session, view.send_id) if s.step == "download"]
    out = []
    for view_id, role in sorted(view.role_by_view.items(), key=lambda kv: kv[1]["role"]):
        step = next((s for s in steps if role["role_id"] in (s.role_ids or [])), None)
        if step is None:
            continue
        unit = units.get(step.unit_key, {})
        out.append(
            {
                "role_id": role["role_id"],
                "role": role["role"],
                "probe_dataset_id": view_id,
                "view_name": view.view_name_by_id.get(view_id),
                "repo_id": unit.get("repo_id"),
                "split": role["split"],
                "commit": step.commit,
                "mistudio_dataset_id": step.mistudio_dataset_id,
                "commit_check": step.commit_check,
                "commit_check_phrase": commit_check.phrase_of(step.commit_check),
            }
        )
    return out


def results_out(row: DetectorResults) -> dict[str, Any]:
    figures = row.figures
    provenance = row.provenance or {}
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
        # 011: null on a snapshot taken before the feature ("not recorded", D51).
        "run_discovery": provenance.get("run_discovery"),
        "commits": provenance.get("commits"),
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
