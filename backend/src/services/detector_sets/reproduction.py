"""The reproduction gate (FR-009.77; FTDD 009 section 6.3; T-49).

Before the first probe-verdict run of a probe on a model, miDataworks scores ONE evaluation role
that miStudio evaluated the probe on, through the same miLLM route and the same one-user-turn
render, and requires the AUROC to fall inside miStudio's reported 95% interval for that set. A
render that does not reproduce miStudio's number is not scoring the probe miStudio measured, so the
run is refused, naming both figures.

- :func:`target_for` finds the recorded evaluation. First the newest results snapshot (009's own
  refresh, read through recorded IDs) whose figures carry the miStudio probe with an AUROC and an
  interval on an in-distribution test or out-of-distribution role (``source: detector_results``).
  Only when no snapshot has one, a reproduction link (``reproduction_links``; option (b) of the
  operator decision of 2026-10-07) naming the probe (``source: linked_mistudio_evaluation``) - an
  imported probe trained on data miDataworks never sent can have no snapshot. Among links, one
  whose rows were proven identical by content hash comes before a counts-only one (2026-10-08
  finding 5: the gate checks one probe on one set of rows, so proven rows are the stronger
  evidence whichever role they hold); then in-distribution (the distribution the probe was cut on),
  then the newer. The chosen target carries ``choice``: why it won, and the alternatives. Nothing
  recorded → ``REPRODUCTION_UNAVAILABLE``, naming BOTH ways through.
- :func:`passed_before` is the cache (FTDD: "before the first run per (probe, model revision)"): a
  probe-verdict run that already passed for the same probe, model, revision, window and input
  form. A revision miLLM did not report is never treated as the same revision.
- :func:`judge` is the comparison itself: ``paired.accept_source("millm", ...)``, the rule the
  results panel applies to the same scores (FR-009.38), called — never re-implemented.

Deviation (recorded in the 009 FTASKS): the FTDD says the result is "cached in the label run's
identity". It is stored in the run's ``endpoint_snapshot["reproduction"]`` instead: putting it in
the identity would change the fingerprint between the first run and every later one, so no later
run could reuse the first run's labels.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, replace
from typing import Any

import pyarrow.parquet as pq
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models.detector_results import DetectorResults
from ...models.detector_send import DetectorSend
from ...models.enums import VersionState
from ...models.label_run import LabelRun
from ...models.reproduction_link import ReproductionLink
from ...models.version import Version
from . import key_labels, paired, results_service
from .label_rules import value_key
from .set_service import split_path

#: Roles miStudio evaluates a probe on, in preference order (FR-009.77 "one evaluation role").
EVALUATION_ROLES: tuple[str, ...] = ("id_test", "ood_eval")
#: Target sources in priority order: a snapshot of 009's own send always wins over a link.
SOURCES: tuple[str, ...] = ("detector_results", "linked_mistudio_evaluation")
#: A link's check level, strongest first (finding 5: rows proven identical outrank role).
LINK_LEVELS: tuple[str, ...] = ("content", "counts_only")

#: The two ways through a ``REPRODUCTION_UNAVAILABLE`` refusal (defect found by the 2026-10-07 live
#: check: the refusal named only the first, which an imported probe can never take).
WAYS_THROUGH: tuple[dict[str, str], ...] = (
    {
        "way": "send_and_train",
        "what": "Send the detector set the probe was trained from, run the probe in miStudio, then "
        "press Refresh results on the Detector sets screen.",
    },
    {
        "way": "link_existing_evaluation",
        "what": "For a probe miDataworks never sent (an imported probe), link a version split "
        "holding the rows miStudio evaluated it on to that recorded evaluation: Link a miStudio "
        "evaluation on the Labeling screen, POST /api/v1/reproduction-links, or the MCP tool "
        "dataworks_create_reproduction_link.",
    },
)


def unavailable_message(mistudio_probe_id: str) -> str:
    return (
        f"No recorded miStudio evaluation of probe {mistudio_probe_id} can be the reproduction "
        "target: no results snapshot records one with an AUROC and an interval on an "
        "in-distribution test or out-of-distribution role, and no reproduction link names the "
        "probe. Two ways through: (a) "
        + WAYS_THROUGH[0]["what"][0].lower()
        + WAYS_THROUGH[0]["what"][1:]
        + " (b) "
        + WAYS_THROUGH[1]["what"][0].lower()
        + WAYS_THROUGH[1]["what"][1:]
    )


class ReproductionUnavailable(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Target:
    """One recorded miStudio evaluation of the probe, and the rows behind it."""

    mistudio_probe_id: str
    snapshot_id: str | None
    set_id: str | None
    send_id: str | None
    probe_dataset_id: str
    role: str
    view_name: str | None
    version_id: str
    split: str
    input_column: str
    label_column: str
    label_mapping: dict[str, str]
    auroc: float
    ci: tuple[float, float]
    n_rows: int
    #: ``detector_results`` (a snapshot) or ``linked_mistudio_evaluation`` (a reproduction link).
    source: str = "detector_results"
    link_id: str | None = None
    #: A link's ``check_level``: ``content`` or ``counts_only``.
    link_check_level: str | None = None
    #: A link's ``scoring_form``: how miStudio scored the rows against how miLLM will.
    scoring_form: dict[str, Any] | None = None
    #: Why this target was chosen, and the other recorded evaluations with their check levels
    #: (2026-10-08 finding 5). Not part of the target's identity (``TARGET_FIELDS``).
    choice: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "link_id": self.link_id,
            "link_check_level": self.link_check_level,
            "scoring_form": self.scoring_form,
            "choice": self.choice,
            "mistudio_probe_id": self.mistudio_probe_id,
            "snapshot_id": self.snapshot_id,
            "set_id": self.set_id,
            "send_id": self.send_id,
            "probe_dataset_id": self.probe_dataset_id,
            "role": self.role,
            "view_name": self.view_name,
            "version_id": self.version_id,
            "split": self.split,
            "mistudio_auroc": self.auroc,
            "mistudio_ci": list(self.ci),
            "n_rows": self.n_rows,
        }


def target_for(session: Session, mistudio_probe_id: str | None) -> Target:
    """The evaluation to reproduce, or :class:`ReproductionUnavailable` naming why there is none."""
    if not mistudio_probe_id:
        raise ReproductionUnavailable(
            "miLLM's copy of this probe records no source miStudio probe "
            "(definition.provenance.probe_id), so there is no miStudio evaluation to reproduce. "
            "Import the probe from a definition miStudio exported."
        )
    candidates: list[tuple[int, int, int, float, Target]] = []
    snapshots = session.execute(
        select(DetectorResults).order_by(DetectorResults.read_at.desc())
    ).scalars()
    for order, snap in enumerate(snapshots):
        for figure in snap.figures:
            if str(figure.get("probe_id")) != mistudio_probe_id or figure.get("gone"):
                continue
            send = session.get(DetectorSend, snap.send_id)
            if send is None:
                continue
            view = results_service.send_view(session, send)
            for entry in figure["sets"]:
                role = view.role_by_view.get(str(entry["probe_dataset_id"]))
                ci = entry.get("ci")
                if (
                    role is None
                    or role["role"] not in EVALUATION_ROLES
                    or entry.get("auroc") is None
                    or not ci
                    or ci[0] is None
                    or ci[1] is None
                ):
                    continue
                target = Target(
                    mistudio_probe_id=mistudio_probe_id,
                    snapshot_id=snap.id,
                    set_id=snap.set_id,
                    send_id=snap.send_id,
                    probe_dataset_id=str(entry["probe_dataset_id"]),
                    role=str(role["role"]),
                    view_name=view.view_name_by_id.get(str(entry["probe_dataset_id"])),
                    version_id=str(role["version_id"]),
                    split=str(role["split"]),
                    input_column=str(role["input_column"]),
                    label_column=str(role["label_column"]),
                    label_mapping={str(k): str(v) for k, v in role["label_mapping"].items()},
                    auroc=float(entry["auroc"]),
                    ci=(float(ci[0]), float(ci[1])),
                    n_rows=int(entry["n_positive"] or 0) + int(entry["n_negative"] or 0),
                )
                candidates.append((0, 0, EVALUATION_ROLES.index(target.role), float(order), target))
    links = session.execute(
        select(ReproductionLink)
        .join(Version, Version.id == ReproductionLink.version_id)
        .where(ReproductionLink.mistudio_probe_id == mistudio_probe_id)
        # A used link outlives its version's delete as evidence (P-15); it is no target.
        .where(Version.state == VersionState.COMPLETED)
        .order_by(ReproductionLink.created_at.desc())
    ).scalars()
    for order, link in enumerate(links):
        candidates.append(
            (
                1,
                LINK_LEVELS.index(link.check_level) if link.check_level in LINK_LEVELS else 99,
                EVALUATION_ROLES.index(link.role),
                float(order),
                target_from_link(link),
            )
        )
    if not candidates:
        raise ReproductionUnavailable(unavailable_message(mistudio_probe_id))
    candidates.sort(key=lambda c: (c[0], c[1], c[2], c[3]))
    chosen = candidates[0][4]
    return replace(chosen, choice=choice_record(chosen, [c[4] for c in candidates[1:]]))


def _brief(target: Target) -> dict[str, Any]:
    return {
        "source": target.source,
        "link_id": target.link_id,
        "snapshot_id": target.snapshot_id,
        "check_level": target.link_check_level,
        "role": target.role,
        "view_name": target.view_name,
        "version_id": target.version_id,
        "split": target.split,
    }


def choice_record(chosen: Target, others: list[Target]) -> dict[str, Any]:
    """Why ``chosen`` won, in the order the rule applies (finding 5): a snapshot of 009's own send
    first; then a link whose rows were proven identical by content hash before a counts-only one
    (the gate checks ONE probe on ONE set of rows, so rows proven the same are the stronger
    evidence, whichever role they hold); then in-distribution before out-of-distribution; then the
    newer."""
    if chosen.source == "detector_results":
        why = "a results snapshot of this detector set's own send always comes first"
    else:
        level = chosen.link_check_level
        why = (
            "a reproduction link whose rows were checked by content hash comes before a "
            "counts-only one"
            if level == "content"
            else "no reproduction link was checked by content hash, so a counts-only link is used"
        )
        same_level = [
            o for o in others if o.source == chosen.source and o.link_check_level == level
        ]
        if same_level:
            why += "; among links at that level, in-distribution comes first, then the newer"
    return {
        "rule": "snapshot, then link check level (content before counts_only), then role "
        "(id_test before ood_eval), then newest",
        "why": why,
        "chosen": _brief(chosen),
        "alternatives": [_brief(o) for o in others[:20]],
        "alternatives_total": len(others),
    }


def target_from_link(link: ReproductionLink) -> Target:
    """A reproduction link as the gate's target: the evaluation miStudio recorded, over the rows of
    the linked split."""
    from . import reproduction_links

    return Target(
        mistudio_probe_id=link.mistudio_probe_id,
        snapshot_id=None,
        set_id=None,
        send_id=None,
        probe_dataset_id=link.probe_dataset_id,
        role=link.role,
        view_name=link.view_name,
        version_id=str(link.version_id),
        split=link.split,
        input_column=link.input_column,
        label_column=link.label_column,
        label_mapping=dict(link.label_mapping),
        auroc=float(link.auroc),
        ci=(float(link.ci_low), float(link.ci_high)),
        n_rows=int(link.n_positive) + int(link.n_negative),
        source="linked_mistudio_evaluation",
        link_id=link.id,
        link_check_level=link.check_level,
        scoring_form=reproduction_links.scoring_form_out(link.scoring_form, link.evidence),
    )


def target_rows(session: Session, target: Target) -> Iterator[tuple[str, dict[str, Any], bool]]:
    """``(row_key, fields, is_positive)`` for every role row that maps to a class, in file order.
    A text value is sent as one user turn; a list value is a chat ``messages`` column."""
    version = session.get(Version, target.version_id)
    if version is None:
        raise ReproductionUnavailable(f"The evaluation role's version {target.version_id} is gone.")
    table = pq.read_table(
        split_path(version, target.split),
        columns=["_dw_row_key", target.input_column, target.label_column],
    )
    mapping = {value_key(k): v for k, v in target.label_mapping.items()}
    for key, value, raw in zip(
        table.column(0).to_pylist(),
        table.column(1).to_pylist(),
        table.column(2).to_pylist(),
        strict=True,
    ):
        cls = mapping.get(value_key(raw)) if raw is not None else None
        if cls not in ("positive", "negative"):
            continue
        fields = {"messages": value} if isinstance(value, list) else {"text": value}
        yield str(key), fields, cls == "positive"


def target_keys(session: Session, target: Target) -> dict[str, Any]:
    """What the gate will count over the target's rows, before it runs (the plan shows it)."""
    return key_labels.summary(
        (key, "positive" if positive else "negative")
        for key, _, positive in target_rows(session, target)
    )


def cache_key(identity: Mapping[str, Any]) -> dict[str, Any]:
    """What makes two runs' reproductions the same check (threshold revision is not in it: the
    gate compares scores, and moving a bar changes no score)."""
    return {
        k: identity[k] for k in ("probe_id", "model_id", "model_revision", "window", "input_form")
    }


def passed_before(
    session: Session, identity: Mapping[str, Any], exclude_run_id: str | None = None
) -> dict[str, Any] | None:
    """A recorded pass for the same check, or None. Never when the revision was not reported."""
    key = cache_key(identity)
    if key["model_revision"] == "not reported":
        return None
    query = select(LabelRun).where(
        LabelRun.kind == "probe_verdict",
        LabelRun.endpoint_snapshot["reproduction"]["state"].astext == "passed",
    )
    if exclude_run_id is not None:
        query = query.where(LabelRun.id != exclude_run_id)
    previous = session.execute(query.order_by(LabelRun.created_at.desc())).scalars()
    for other in previous:
        if cache_key(other.labeler_identity) == key:
            found = dict(other.endpoint_snapshot["reproduction"])
            return {**found, "state": "passed", "cached_from_run_id": other.id}
    return None


#: What a failed gate asks for before the same check runs again (2026-10-08 finding 3).
RETRY_GUIDANCE = (
    "Only a pass is cached, so the same check would fail the same way. Change something first: "
    "another reproduction link or target, another window or input form (for example a chat column "
    "parsed with chat_json_parser), or a different model revision in miLLM. Or start the run with "
    "reproduction_retry_reason saying why the same check should run again; the reason is recorded "
    "on the run."
)
#: The fields that name WHICH recorded evaluation a gate compared with: a different target is a
#: changed check, even under the same probe, model, revision, window and input form.
TARGET_FIELDS: tuple[str, ...] = (
    "source",
    "link_id",
    "snapshot_id",
    "probe_dataset_id",
    "role",
    "version_id",
    "split",
)


def target_identity(record: Mapping[str, Any]) -> dict[str, Any]:
    return {k: record.get(k) for k in TARGET_FIELDS}


def failed_before(
    session: Session,
    identity: Mapping[str, Any],
    target: Target,
    exclude_run_id: str | None = None,
) -> dict[str, Any] | None:
    """The newest recorded FAILURE of the same check against the same target, or None.

    The same check is :func:`cache_key` (probe, model, revision, window, input form) AND the same
    target (:data:`TARGET_FIELDS`): change any of them and the gate runs again. A revision miLLM did
    not report is never "the same revision" (as for :func:`passed_before`), so it never matches."""
    key = cache_key(identity)
    if key["model_revision"] == "not reported":
        return None
    wanted = target_identity(target.as_dict())
    query = select(LabelRun).where(
        LabelRun.kind == "probe_verdict",
        LabelRun.endpoint_snapshot["reproduction"]["state"].astext == "failed",
    )
    if exclude_run_id is not None:
        query = query.where(LabelRun.id != exclude_run_id)
    for other in session.execute(query.order_by(LabelRun.created_at.desc())).scalars():
        found = dict(other.endpoint_snapshot["reproduction"])
        if cache_key(other.labeler_identity) == key and target_identity(found) == wanted:
            return {
                **found,
                "state": "failed",
                "failed_run_id": other.id,
                "failed_at": other.completed_at.isoformat() if other.completed_at else None,
                "retry": RETRY_GUIDANCE,
            }
    return None


def judge(
    target: Target,
    scores: list[float],
    labels: list[bool],
    dropped: int,
    keys: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The gate's verdict as a record, with both figures and what was counted (``keys``: the
    rows, the distinct keys scored and the keys whose copies carry both classes)."""
    accepted = paired.accept_source(
        "millm", scores, labels, reported_auroc=target.auroc, reported_ci=target.ci
    )
    return {
        **target.as_dict(),
        "state": "passed" if accepted.accepted else "failed",
        "millm_auroc": accepted.auroc,
        "rows_scored": len(scores),
        "rows_dropped": dropped,
        "row_keys": keys,
        "reason": accepted.reason,
    }


def failure_message(record: Mapping[str, Any]) -> str:
    lo, hi = record["mistudio_ci"]
    target = ""
    if record.get("source") == "linked_mistudio_evaluation":
        target = (
            f" The target is reproduction link {record.get('link_id')} (rows checked: "
            f"{'content hash' if record.get('link_check_level') == 'content' else 'counts only'})."
        )
    keys = record.get("row_keys") or {}
    conflict = key_labels.conflict_sentence(keys) if keys else None
    if keys:
        target += f" {keys['rows']} rows, {keys['row_keys']} distinct inputs, each scored once." + (
            f" {conflict}" if conflict else ""
        )
    return (
        f"The reproduction check failed: scoring {record['rows_scored']} rows of "
        f"{record.get('view_name') or record['role']} through miLLM gave AUROC "
        f"{record['millm_auroc']:.4f}, outside miStudio's reported {record['mistudio_auroc']:.4f} "
        f"[{lo:.4f}, {hi:.4f}]. miLLM is not scoring the probe miStudio measured (the render, the "
        "model or its precision differ), so no probe verdict is written." + target
    )


ScoreRow = Callable[[str, dict[str, Any]], float | None]


def run_gate(session: Session, target: Target, score_row: ScoreRow) -> dict[str, Any]:
    """Score the target's rows and judge them (2026-10-08 finding 2: the same count as the run and
    the link). Each distinct row key is scored ONCE and its score applies to every copy, as T-07
    says a label does; each copy keeps its own class, as miStudio counted it. A key miLLM did not
    score drops every copy, counted. Keys whose copies carry both classes are named in the record
    (``row_keys.conflicting``), never resolved to one label."""
    scores: list[float] = []
    labels: list[bool] = []
    keyed: list[tuple[str, str]] = []
    scored: dict[str, float | None] = {}
    dropped = 0
    for key, fields, positive in target_rows(session, target):
        keyed.append((key, "positive" if positive else "negative"))
        if key not in scored:
            scored[key] = score_row(key, fields)
        score = scored[key]
        if score is None:
            dropped += 1
            continue
        scores.append(float(score))
        labels.append(positive)
    return judge(target, scores, labels, dropped, key_labels.summary(keyed))
