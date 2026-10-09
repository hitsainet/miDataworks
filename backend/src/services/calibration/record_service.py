"""Calibration records: start (API side) and compute (worker side) (FR-006.6 – FR-006.21,
FR-006.36; FTDD 006 section 2.2).

Guarantees:
- ``start`` refuses before queueing: the run must be ``completed``, its input version must be the
  set's version, its question must be the set's question, and every human-labeled set row must be
  scored (``ROWS_NOT_SCORED`` names the count);
- ``compute`` reads, never scores (no model call). It calls the pure layer, checks cancellation
  between stages, and writes the record, its checks and its verdict in ONE transaction at the
  end; a cancelled or failed job writes nothing;
- ``metrics_sha256`` is computed over the canonical bytes of the metrics document, so recomputing
  the same inputs gives the same hash; every seed is recorded in ``settings``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Any

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ...core.agent_origin import Who
from ...core.cancellation import CancelCheck, record_progress
from ...core.canonical_json import canonical_sha256
from ...core.errors import ConflictError, NotFoundError
from ...core.ids import new_id
from ...core.job_kinds import get_job_kind
from ...models.calibration import (
    CalibrationCheck,
    CalibrationRecord,
    CalibrationSet,
    CalibrationTarget,
    CalibrationVerdict,
)
from ...models.decision_template import DecisionTemplate
from ...models.job import Job
from ...models.label_run import LabelRun
from ...models.version import Version
from ...schemas.calibration import (
    CalibrationRecordOut,
    CalibrationRecordStart,
    CheckOut,
    JobStarted,
    VerdictOut,
)
from . import ceiling as ceiling_mod
from . import metrics as m
from .arrays import CalibrationArrays, load_arrays, scores_by_key, unscored_count_sql
from .checks import CheckContext, CheckResult, PairedFigure
from .constants import (
    BOOTSTRAP_RESAMPLES,
    CODE_VERSION,
    PERMUTATIONS,
    RATER_DRAWS,
    RELIABILITY_BINS,
    SEED_AUROC,
    SEED_CEILING_BOOTSTRAP,
    SEED_CEILING_DRAWS,
    SEED_PAIRS,
    SEED_PERMUTATION,
)
from .domain import content_lengths, domain_warnings
from .registry import run_checks
from .verdict import Figures, Verdict, decide

logger = logging.getLogger(__name__)

JOB_KIND = "calibration_compute"


# --------------------------------------------------------------------------------------------
# Start (API)
# --------------------------------------------------------------------------------------------


def _dispatch() -> list[str]:
    from ...core.database import get_sync_db
    from ..job_service import dispatch_queued

    with get_sync_db() as session:
        return dispatch_queued(session)


async def validate_start(
    db: AsyncSession, body: CalibrationRecordStart
) -> tuple[LabelRun, CalibrationSet]:
    run = await db.get(LabelRun, body.label_run_id)
    if run is None:
        raise NotFoundError(f"No label run {body.label_run_id}.", code="LABEL_RUN_NOT_FOUND")
    cal = await db.get(CalibrationSet, body.calibration_set_id)
    if cal is None:
        raise NotFoundError(
            f"No calibration set {body.calibration_set_id}.", code="CALIBRATION_SET_NOT_FOUND"
        )
    if run.state != "completed":
        raise ConflictError(
            f"Label run {run.id} is {run.state}. Let it finish (or resume it), then compute again.",
            code="RUN_NOT_COMPLETED",
            details={"state": run.state},
        )
    if str(run.input_version_id) != str(cal.version_id):
        raise ConflictError(
            f"Label run {run.id} labeled version {run.input_version_id}, but the calibration set "
            f"is on version {cal.version_id}. Label the set's version with this labeler first.",
            code="RUN_VERSION_MISMATCH",
            details={"run_version": str(run.input_version_id), "set_version": str(cal.version_id)},
        )
    if run.question != cal.question:
        raise ConflictError(
            "The label run asked a different question from the calibration set. A labeler is "
            "calibrated for the exact question text it was asked.",
            code="QUESTION_MISMATCH",
            details={"run_question": run.question, "set_question": cal.question},
        )
    missing = await db.run_sync(
        lambda s: unscored_count_sql(s, cal.id, run.id, list(cal.label_set))
    )
    if missing:
        raise ConflictError(
            f"{missing} calibration rows have no label in this run. Resume the label run, then "
            "compute again.",
            code="ROWS_NOT_SCORED",
            details={"missing": missing},
        )
    return run, cal


async def start(db: AsyncSession, body: CalibrationRecordStart, who: Who) -> JobStarted:
    run, cal = await validate_start(db, body)
    job = Job(
        id=new_id("job"),
        kind=JOB_KIND,
        status="queued",
        progress=0.0,
        params={"label_run_id": run.id, "calibration_set_id": cal.id},
        started_by=who.who,
        started_by_origin=who.origin,
    )
    db.add(job)
    await db.commit()
    _dispatch()
    logger.info(
        "calibration.record.started job=%s set=%s run=%s identity=%s",
        job.id,
        cal.id,
        run.id,
        run.labeler_identity_hash,
    )
    return JobStarted(job_id=job.id, room=get_job_kind(JOB_KIND).room(job.id))


# --------------------------------------------------------------------------------------------
# Compute (worker)
# --------------------------------------------------------------------------------------------


def current_target(session: Session, qh: str) -> CalibrationTarget | None:
    return session.execute(
        select(CalibrationTarget)
        .where(CalibrationTarget.question_hash == qh)
        .order_by(CalibrationTarget.created_at.desc(), CalibrationTarget.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def score_kind(arrays: CalibrationArrays, labeled: np.ndarray) -> str:
    idx = np.flatnonzero(labeled)
    if len(idx) and all(arrays.probabilities[i] is not None for i in idx):
        return "probability"
    if len(idx) and any(arrays.distributions[i] for i in idx):
        return "distribution"
    return "discrete"


def _labeler_label(
    probability: float | None, outcome: str | None, run: LabelRun, label_set: Sequence[str]
) -> str | None:
    if outcome == "positive" or outcome == label_set[0]:
        return label_set[0]
    if outcome == "negative" or (len(label_set) > 1 and outcome == label_set[1]):
        return label_set[1]
    if outcome in label_set:
        return outcome
    return None


def compute_figures(
    arrays: CalibrationArrays,
    cal: CalibrationSet,
    run: LabelRun,
    check: CancelCheck | None = None,
    *,
    by_key: dict[str, float] | None = None,
) -> tuple[dict[str, Any], Figures, list[CheckResult], dict[str, Any]]:
    """Metrics document, gate figures, check results and settings. Pure but for its inputs."""
    label_set = list(cal.label_set)
    mapping = cal.mapping
    has_score = ~np.isnan(arrays.scores)
    is_ref = np.array(arrays.is_reference, dtype=bool)
    human = arrays.human
    labeled = np.array([h is not None for h in human], dtype=bool) & ~is_ref & has_score
    idx = np.flatnonzero(labeled)
    y = np.array([1 if human[i] == label_set[0] else 0 for i in idx], dtype=np.int64)
    p = arrays.scores[idx]
    keys = [arrays.row_keys[i] for i in idx]
    kind = score_kind(arrays, labeled)
    reasons: dict[str, str] = {}
    metrics_doc: dict[str, Any] = {"score_kind": kind, "n_rows": len(arrays), "n_labeled": len(idx)}

    auroc_iv: m.Interval | None = None
    if m.both_classes(y):
        auroc_iv = m.bootstrap_auroc_ci(y, p, seed=SEED_AUROC)
        metrics_doc["auroc"] = {
            **auroc_iv.as_dict(),
            "n_pos": int(y.sum()),
            "n_neg": int(len(y) - y.sum()),
        }
    else:
        missing = "positives" if int(y.sum()) == 0 else "negatives"
        metrics_doc["auroc"] = None
        reasons["auroc"] = f"AUROC needs both classes; 0 {missing} after mapping"
    if check is not None:
        check.raise_if_cancelled()

    if len(label_set) > 2:
        dists = [arrays.distributions[i] or {} for i in idx]
        metrics_doc["auroc_by_class"] = m.macro_auroc_ovr(
            [human[i] or "" for i in idx], dists, label_set
        )
    else:
        metrics_doc["auroc_by_class"] = None

    # Reliability and bands: probabilities only (FR-006.11, FR-006.12).
    if kind == "probability" and len(idx):
        metrics_doc["reliability"] = m.reliability_bins(y, p, bins=RELIABILITY_BINS)
    else:
        metrics_doc["reliability"] = None
        reasons["reliability"] = "score is discrete" if kind != "probability" else "no rows"
    if (
        kind == "probability"
        and run.threshold_positive is not None
        and run.threshold_negative is not None
        and len(idx)
    ):
        metrics_doc["band_shares"] = m.band_shares(
            y,
            p,
            threshold_positive=run.threshold_positive,
            threshold_negative=run.threshold_negative,
        )
    else:
        metrics_doc["band_shares"] = None
        reasons["band_shares"] = "the label run has no thresholds or the score is discrete"
    labeler_labels = [
        _labeler_label(arrays.probabilities[i], arrays.outcomes[i], run, label_set) for i in idx
    ]
    metrics_doc["kappa"] = m.kappa_on_kept(labeler_labels, [human[i] for i in idx])
    if metrics_doc["kappa"] is None:
        reasons["kappa"] = "fewer than two kept rows or one label only"

    # Ceiling (FR-006.8, FR-006.9).
    present = {"auroc"}
    ceiling_result: ceiling_mod.CeilingResult | None = None
    draws: list[ceiling_mod.Draw] | None = None
    ratings_rule = mapping.get("ratings")
    rule = mapping.get("human_label") or {}
    if ratings_rule and rule.get("rule") == "numeric":
        present |= {"ceiling", "comparison_auroc"}
        rows = [arrays.ratings[i] if has_score[i] else None for i in range(len(arrays))]
        R, counts = ceiling_mod.ratings_matrix(rows)
        draws = ceiling_mod.held_out_draws(
            R,
            counts,
            positive_at_or_above=float(rule["positive_at_or_above"]),
            negative_at_or_below=float(rule["negative_at_or_below"]),
            draws=RATER_DRAWS,
            seed=SEED_CEILING_DRAWS,
        )
        ceiling_result = ceiling_mod.ceiling_and_comparison(
            draws, np.nan_to_num(arrays.scores), n_rows=len(arrays), seed=SEED_CEILING_BOOTSTRAP
        )
        if ceiling_result is None:
            reasons["ceiling"] = "no draw's consensus has both classes"
    else:
        reasons["ceiling"] = (
            "Not available: no per-rater column"
            if not ratings_rule
            else "Not available: the per-rater ceiling needs a numeric human-label rule"
        )
    metrics_doc["ceiling"] = (
        {
            "value": ceiling_result.ceiling.value,
            "ci_low": ceiling_result.ceiling.ci_low,
            "ci_high": ceiling_result.ceiling.ci_high,
            "draws": ceiling_result.draws,
            "n_mean": ceiling_result.n_mean,
            "draw_values": ceiling_result.draw_values,
        }
        if ceiling_result is not None
        else None
    )
    metrics_doc["comparison_auroc"] = (
        {
            "value": ceiling_result.comparison.value,
            "ci_low": ceiling_result.comparison.ci_low,
            "ci_high": ceiling_result.comparison.ci_high,
        }
        if ceiling_result is not None
        else None
    )
    if check is not None:
        check.raise_if_cancelled()

    # Same-group pairs (FR-006.10).
    paired: PairedFigure | None = None
    if mapping.get("group"):
        present.add("paired")
        labels_for_pairs = [int(v) for v in y]
        groups = [arrays.groups[i] for i in idx]
        strata = [arrays.strata[i] for i in idx] if mapping.get("strata") else None
        pos, neg, code = m.cross_label_pairs(groups, labels_for_pairs, strata)
        if len(pos):
            wins = m.paired_accuracy(p, pos, neg)
            interval = m.cluster_bootstrap_ci(wins, code, seed=SEED_PAIRS)
            paired = PairedFigure(wins, code, interval)
            metrics_doc["paired"] = {
                "value": interval.value,
                "ci_low": interval.ci_low,
                "ci_high": interval.ci_high,
                "pairs": int(len(pos)),
                "groups": int(len(np.unique(code))),
            }
        else:
            metrics_doc["paired"] = None
            reasons["paired"] = "no group holds both a positive and a negative row"
    else:
        metrics_doc["paired"] = None
        reasons["paired"] = "Not available: no group column"

    # Reference diagnostic and its control (FR-006.41).
    reference: m.ReferenceWins | None = None
    if mapping.get("reference") and mapping.get("group"):
        present.add("reference_diagnostic")
        scored = np.flatnonzero(has_score)
        reference = m.reference_pairs(
            [arrays.groups[i] for i in scored],
            [arrays.is_reference[i] for i in scored],
            [None if human[i] is None else (1 if human[i] == label_set[0] else 0) for i in scored],
            arrays.scores[scored],
        )
        if len(reference.positive) and len(reference.negative):
            control = m.cluster_bootstrap_ci(
                reference.negative, reference.negative_group, seed=SEED_PAIRS
            )
            metrics_doc["reference_diagnostic"] = {
                "value": float(reference.positive.mean()),
                "control": control.value,
                "control_ci": [control.ci_low, control.ci_high],
                "n_pos": int(len(reference.positive)),
                "n_neg": int(len(reference.negative)),
            }
        else:
            metrics_doc["reference_diagnostic"] = None
            reasons["reference_diagnostic"] = "no labeled row has a reference in its group"
    else:
        metrics_doc["reference_diagnostic"] = None
        reasons["reference_diagnostic"] = "Not available: no reference selector"
    if check is not None:
        check.raise_if_cancelled()

    ctx = CheckContext(
        y=y,
        scores=p,
        row_keys=keys,
        scores_by_key=by_key if by_key is not None else {},
        all_row_keys=arrays.row_keys,
        auroc=auroc_iv,
        ceiling=ceiling_result,
        draws=draws,
        paired=paired,
        reference=reference,
        shortcut=None,
    )
    checks = run_checks(ctx, present)
    metrics_doc["reasons"] = reasons
    figs = Figures(
        auroc_iv,
        ceiling_result.ceiling if ceiling_result else None,
        ceiling_result.comparison if ceiling_result else None,
    )
    settings = {
        "code_version": CODE_VERSION,
        "seeds": {
            "auroc": SEED_AUROC,
            "pairs": SEED_PAIRS,
            "ceiling_draws": SEED_CEILING_DRAWS,
            "ceiling_bootstrap": SEED_CEILING_BOOTSTRAP,
            "permutation": SEED_PERMUTATION,
        },
        "resamples": BOOTSTRAP_RESAMPLES,
        "rater_draws": RATER_DRAWS,
        "reliability_bins": RELIABILITY_BINS,
        "permutations": PERMUTATIONS,
    }
    return metrics_doc, figs, checks, settings


def _conformance(session: Session, run: LabelRun) -> dict[str, Any] | None:
    """FR-006.17: a conformance result recorded on 005's decision template, for display."""
    if run.template_id is None:
        return None
    template = session.get(DecisionTemplate, run.template_id)
    body = template.body if template is not None else None
    value = body.get("conformance") if isinstance(body, dict) else None
    if not isinstance(value, dict):
        return None
    return {"template_id": template.id if template else None, **value}


def _domain(session: Session, cal: CalibrationSet, run: LabelRun) -> list[dict[str, Any]]:
    version = session.get(Version, cal.version_id)
    if version is None:
        return []
    others = session.execute(
        select(LabelRun.input_version_id)
        .where(
            LabelRun.labeler_identity_hash == run.labeler_identity_hash,
            LabelRun.state == "completed",
            LabelRun.input_version_id != cal.version_id,
        )
        .distinct()
    ).scalars()
    lengths: dict[str, Any] = {}
    for vid in others:
        other = session.get(Version, vid)
        if other is not None:
            lengths[str(vid)] = content_lengths(other.splits, other.column_roles)
    if not lengths:
        return []
    return domain_warnings(content_lengths(version.splits, version.column_roles), lengths)


def compute(session: Session, job: Job) -> CalibrationRecord:
    """The worker's body: everything between a claimed job and a committed record."""
    started = time.monotonic()
    params = dict(job.params)
    cal = session.get(CalibrationSet, params["calibration_set_id"])
    run = session.get(LabelRun, params["label_run_id"])
    if cal is None or run is None:
        raise LookupError("the calibration set or the label run no longer exists")
    check = CancelCheck(job.id, min_interval_s=1.0)
    record_progress(job.id, progress=5.0, message="Loading human labels and scores", db=session)
    arrays = load_arrays(session, cal.id, run.id, list(cal.label_set))
    by_key = scores_by_key(session, run.id, list(cal.label_set))
    check.raise_if_cancelled()
    record_progress(job.id, progress=20.0, message="Computing AUROC, ceiling and pairs", db=session)
    metrics_doc, figs, checks, settings = compute_figures(arrays, cal, run, check, by_key=by_key)
    record_progress(job.id, progress=80.0, message="Checks and verdict", db=session)
    target = current_target(session, cal.question_hash)
    verdict: Verdict = decide(figs, checks, target.target if target is not None else None)
    warnings = _domain(session, cal, run)
    dropped = (metrics_doc.get("auroc") or {}).get("dropped")
    if dropped:
        warnings.append(
            {
                "kind": "dropped_resamples",
                "count": dropped,
                "message": f"{dropped} bootstrap " "resamples lacked a class and were dropped.",
            }
        )
    check.raise_if_cancelled()
    record = CalibrationRecord(
        id=new_id("cr"),
        calibration_set_id=cal.id,
        label_run_id=run.id,
        labeler_identity=run.labeler_identity,
        labeler_identity_hash=run.labeler_identity_hash,
        labeler_fingerprint=run.labeler_fingerprint,
        score_kind=metrics_doc["score_kind"],
        metrics=metrics_doc,
        metrics_sha256=canonical_sha256(metrics_doc),
        settings={**settings, "conformance": _conformance(session, run)},
        warnings=warnings,
        job_id=job.id,
        created_by=job.started_by,
        created_by_origin=job.started_by_origin,
    )
    session.add(record)
    session.flush()
    session.add_all(
        CalibrationCheck(
            record_id=record.id,
            check_id=c.check_id,
            metric_id=c.metric_id,
            check_version=c.version,
            result=c.result,
            statistic=c.statistic,
            rule=c.rule,
            reason=c.reason,
        )
        for c in checks
    )
    session.add(
        CalibrationVerdict(
            record_id=record.id,
            verdict=verdict.verdict,
            rule=verdict.rule,
            numbers=verdict.numbers,
            target_id=target.id if target is not None else None,
        )
    )
    session.commit()
    logger.info(
        "calibration.record.completed record=%s set=%s identity=%s verdict=%s seconds=%.1f",
        record.id,
        cal.id,
        run.labeler_identity_hash,
        verdict.verdict,
        time.monotonic() - started,
    )
    return record


# --------------------------------------------------------------------------------------------
# Read
# --------------------------------------------------------------------------------------------


def figures_of(record: CalibrationRecord) -> Figures:
    """Rebuild the verdict's inputs from a stored record (the target-change preview)."""

    def iv(d: dict[str, Any] | None) -> m.Interval | None:
        if not d:
            return None
        return m.Interval(
            float(d["value"]),
            float(d["ci_low"]),
            float(d["ci_high"]),
            int(d.get("n", 0) or 0),
            int(d.get("resamples", 0) or 0),
            int(d.get("dropped", 0) or 0),
        )

    metrics_doc = record.metrics
    return Figures(
        iv(metrics_doc.get("auroc")),
        iv(metrics_doc.get("ceiling")),
        iv(metrics_doc.get("comparison_auroc")),
    )


def checks_of(rows: Sequence[CalibrationCheck]) -> list[CheckResult]:
    return [
        CheckResult(
            r.check_id,
            r.check_version,
            r.metric_id,
            r.result,  # type: ignore[arg-type]
            r.statistic,
            r.rule,
            r.reason,
        )
        for r in rows
    ]


def record_out(
    record: CalibrationRecord,
    cal: CalibrationSet,
    checks: Sequence[CalibrationCheck],
    verdict: CalibrationVerdict,
) -> CalibrationRecordOut:
    order = {"fail": 0, "not_applicable": 1, "pass": 2}
    settings = dict(record.settings)
    conformance = settings.pop("conformance", None)
    return CalibrationRecordOut(
        id=record.id,
        calibration_set_id=record.calibration_set_id,
        label_run_id=record.label_run_id,
        question=cal.question,
        question_hash=cal.question_hash,
        labeler_identity=record.labeler_identity,
        labeler_identity_hash=record.labeler_identity_hash,
        labeler_fingerprint=record.labeler_fingerprint,
        score_kind=record.score_kind,
        metrics=record.metrics,
        metrics_sha256=record.metrics_sha256,
        checks=[
            CheckOut(
                check_id=c.check_id,
                check_version=c.check_version,
                metric_id=c.metric_id,
                result=c.result,
                statistic=c.statistic,
                rule=c.rule,
                reason=c.reason,
            )
            for c in sorted(checks, key=lambda c: (order.get(c.result, 3), c.metric_id, c.check_id))
        ],
        verdict=VerdictOut(
            verdict=verdict.verdict,
            rule=verdict.rule,
            numbers=verdict.numbers,
            target_id=verdict.target_id,
        ),
        warnings=record.warnings,
        settings=settings,
        conformance=conformance,
        created_by=record.created_by,
        created_by_origin=record.created_by_origin,
        created_at=record.created_at,
    )


async def load_out(db: AsyncSession, record: CalibrationRecord) -> CalibrationRecordOut:
    cal = await db.get(CalibrationSet, record.calibration_set_id)
    verdict = await db.get(CalibrationVerdict, record.id)
    assert cal is not None and verdict is not None
    checks = (
        await db.execute(select(CalibrationCheck).where(CalibrationCheck.record_id == record.id))
    ).scalars()
    return record_out(record, cal, list(checks), verdict)


async def get_record(db: AsyncSession, record_id: str) -> CalibrationRecordOut:
    record = await db.get(CalibrationRecord, record_id)
    if record is None:
        raise NotFoundError(f"No calibration record {record_id}.", code="RECORD_NOT_FOUND")
    return await load_out(db, record)


async def list_records(
    db: AsyncSession,
    *,
    labeler: str | None,
    question_hash: str | None,
    offset: int,
    limit: int,
) -> tuple[list[CalibrationRecordOut], int]:
    query = select(CalibrationRecord)
    if labeler:
        query = query.where(CalibrationRecord.labeler_identity_hash == labeler)
    if question_hash:
        query = query.join(
            CalibrationSet, CalibrationSet.id == CalibrationRecord.calibration_set_id
        ).where(CalibrationSet.question_hash == question_hash)
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (
        await db.execute(
            query.order_by(CalibrationRecord.created_at.desc(), CalibrationRecord.id.desc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars()
    return [await load_out(db, r) for r in rows], int(total)
