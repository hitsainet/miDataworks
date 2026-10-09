"""The probe-judge agreement report (FR-009.52 - FR-009.56; FTDD 009 section 4.1; FTID 009
section 7.8; T-48).

Both labelers are read from label runs that already exist in miDataworks: a ``probe_verdict`` run
(the probe, scored through miLLM) and a judge run (005). No miStudio judge run is started — this
module imports no miStudio client (T-48; ``test_agreement_calls_no_mistudio``).

On the SAME rows (row keys present in both runs and in the reference):

- both AUROCs against the reference, each with a seeded row bootstrap interval (2,000 resamples);
- percent agreement and Cohen's kappa over rows where both verdicts are non-null and not
  provisional, with the dropped counts named;
- ``judge_is_training_labeler``: the judge's labeler identity is the one bound to the training
  version — then the judge is not an independent reference, and the report says so.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pyarrow.parquet as pq
from sklearn.metrics import cohen_kappa_score, roc_auc_score
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.ids import new_id
from ...models.detector_results import AgreementReport
from ...models.label import Label
from ...models.label_run import LabelRun
from ...models.version import Version
from .errors import DetectorSetError
from .label_rules import value_key
from .set_service import Who, split_path

BOOTSTRAP = 2000
SEED = 20261006
TRAINING_LABELER_CAVEAT = (
    "The judge is the labeler whose labels trained the probe, so agreement measures how well the "
    "probe imitates its teacher, not whether either is right."
)


def _run(session: Session, run_id: str) -> LabelRun:
    run = session.get(LabelRun, run_id)
    if run is None:
        raise DetectorSetError("report_not_found", f"No label run {run_id}.")
    return run


def _labels(session: Session, run_id: str) -> dict[str, Label]:
    return {
        row.row_key: row
        for row in session.execute(select(Label).where(Label.label_run_id == run_id)).scalars()
    }


def _score(label: Label) -> float | None:
    value = label.parsed_value
    if isinstance(value, dict) and value.get("score") is not None:
        return float(value["score"])
    return label.probability


def _verdict(label: Label | None) -> int | None:
    if label is None or label.provisional or label.outcome not in ("positive", "negative"):
        return None
    return 1 if label.outcome == "positive" else 0


def _auroc_ci(y: np.ndarray, s: np.ndarray) -> dict[str, Any] | None:
    if y.size == 0 or y.min() == y.max():
        return None
    rng = np.random.default_rng(SEED)
    stats = []
    for _ in range(BOOTSTRAP):
        pick = rng.integers(0, y.size, y.size)
        if y[pick].min() != y[pick].max():
            stats.append(roc_auc_score(y[pick], s[pick]))
    lo, hi = np.quantile(stats, [0.025, 0.975])
    return {
        "value": round(float(roc_auc_score(y, s)), 4),
        "ci_low": round(float(lo), 4),
        "ci_high": round(float(hi), 4),
        "n": int(y.size),
        "n_positive": int(y.sum()),
    }


def _reference(
    session: Session, version: Version, split: str, ref: Mapping[str, Any]
) -> dict[str, int]:
    """Row key -> 1/0 reference label."""
    if ref.get("kind") == "label_column":
        column = str(ref["column"])
        positive = {value_key(v) for v in ref.get("positive_values") or []}
        negative = {value_key(v) for v in ref.get("negative_values") or []}
        table = pq.read_table(split_path(version, split), columns=["_dw_row_key", column])
        out: dict[str, int] = {}
        for key, raw in zip(table.column(0).to_pylist(), table.column(1).to_pylist(), strict=True):
            if raw is None:
                continue
            k = value_key(raw)
            if k in positive:
                out[key] = 1
            elif k in negative:
                out[key] = 0
        return out
    if ref.get("kind") == "label_run":
        return {
            k: v
            for k, lab in _labels(session, str(ref["label_run_id"])).items()
            if (v := _verdict(lab)) is not None
        }
    raise DetectorSetError(
        "rows_disjoint",
        "The reference is a label column (with its positive and negative values) or a label run.",
    )


def compute(session: Session, body: Mapping[str, Any], who: Who) -> AgreementReport:
    version = session.get(Version, str(body["version_id"]))
    if version is None:
        raise DetectorSetError("version_incomplete", f"No version {body['version_id']}.")
    probe_run = _run(session, str(body["probe_label_run_id"]))
    judge_run = _run(session, str(body["judge_label_run_id"]))
    split = str(body["split"])
    reference = _reference(session, version, split, body["reference"])
    keys_in_split = set(
        pq.read_table(split_path(version, split), columns=["_dw_row_key"]).column(0).to_pylist()
    )
    probe = _labels(session, probe_run.id)
    judge = _labels(session, judge_run.id)
    rows = sorted(keys_in_split & set(probe) & set(judge) & set(reference))
    if not rows:
        raise DetectorSetError(
            "rows_disjoint",
            "The probe run, the judge run and the reference share no rows of this split; score "
            "the same rows with both, then compare.",
        )
    figures: dict[str, Any] = {"rows_compared": len(rows)}
    for name, labels in (("probe", probe), ("judge", judge)):
        scored = [(reference[k], _score(labels[k])) for k in rows if _score(labels[k]) is not None]
        figures[f"{name}_auroc"] = _auroc_ci(
            np.array([a for a, _ in scored]), np.array([b for _, b in scored], dtype=float)
        )
        figures[f"{name}_rows_without_score"] = len(rows) - len(scored)
    pv = [_verdict(probe[k]) for k in rows]
    jv = [_verdict(judge[k]) for k in rows]
    both = [(a, b) for a, b in zip(pv, jv, strict=True) if a is not None and b is not None]
    figures["dropped"] = {
        "probe_null_or_provisional": sum(1 for a in pv if a is None),
        "judge_null_or_provisional": sum(1 for b in jv if b is None),
    }
    if both:
        a = np.array([x for x, _ in both])
        b = np.array([x for _, x in both])
        figures["agreement"] = round(float(np.mean(a == b)), 4)
        figures["kappa"] = (
            round(float(cohen_kappa_score(a, b)), 4) if len(set(a) | set(b)) > 1 else None
        )
        figures["n_agreement"] = len(both)
        figures["disagreements"] = [
            k
            for k, p, j in zip(rows, pv, jv, strict=True)
            if p is not None and j is not None and p != j
        ][:5000]
    else:
        figures.update({"agreement": None, "kappa": None, "n_agreement": 0, "disagreements": []})
    training_version = body.get("training_version_id")
    judge_is_teacher = False
    if training_version:
        tv = session.get(Version, str(training_version))
        if tv is not None:
            bound = [str(x["id"]) for x in tv.bindings if x["kind"] == "label_run"]
            hashes = set(
                session.execute(
                    select(LabelRun.labeler_identity_hash).where(LabelRun.id.in_(bound))
                ).scalars()
            )
            judge_is_teacher = judge_run.labeler_identity_hash in hashes
    figures["caveat"] = TRAINING_LABELER_CAVEAT if judge_is_teacher else None
    row = AgreementReport(
        id=new_id("agr"),
        version_id=version.id,
        split=split,
        probe_label_run_id=probe_run.id,
        judge_label_run_id=judge_run.id,
        reference=dict(body["reference"]),
        figures=figures,
        judge_is_training_labeler=judge_is_teacher,
        created_by=who.who,
        created_by_origin=who.origin,
    )
    session.add(row)
    session.commit()
    return row


def get(session: Session, report_id: str) -> AgreementReport:
    row = session.get(AgreementReport, report_id)
    if row is None:
        raise DetectorSetError("report_not_found", f"No agreement report {report_id}.")
    return row


def report_out(row: AgreementReport) -> dict[str, Any]:
    return {
        "id": row.id,
        "version_id": row.version_id,
        "split": row.split,
        "probe_label_run_id": row.probe_label_run_id,
        "judge_label_run_id": row.judge_label_run_id,
        "reference": row.reference,
        "figures": row.figures,
        "judge_is_training_labeler": row.judge_is_training_labeler,
        "wording": "compared with a judge",
        "created_by": row.created_by,
        "created_by_origin": row.created_by_origin,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
