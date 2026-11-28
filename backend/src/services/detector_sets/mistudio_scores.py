"""miWorkbench's per-row scores as a paired-score source (feature 011, XR-2; FR-011.12 - FR-011.18;
FTDD 011 section 6, D35, D50; R-P.08 - R-P.12).

Two halves, so no HTTP runs on the event loop and no database read runs in the HTTP thread:

1. :func:`fetch` (the refresh's worker thread): every page of ONE evaluation's scores — the
   evaluation behind the figure, by its recorded ``evaluation_id`` (D36). A refusal from miWorkbench
   (``409 positions_not_recorded``, a ``404``, a page of another evaluation) comes back as a
   :class:`Refusal` with miWorkbench's words, verbatim; an unreachable or non-JSON miWorkbench still fails
   the whole refresh (502, the previous snapshot kept).
2. :func:`join` (the session): each served row's ``position`` in miWorkbench's downloaded split is
   mapped to ``(_dw_row_key, _dw_occurrence)`` through the split miDataworks PUBLISHED at that
   commit — the send's publish build file (D35, D50). Rows are joined by POSITION, never by their
   order in the response (D2, D14): miWorkbench drops rows (filters, failed renders), so positions are
   sparse.

The source is refused, with its reason, when miWorkbench's commit is not the publish commit, when the
download is not the one this send recorded, when the build file is gone, when a position is out of
range, or when miWorkbench's label for a row disagrees with this role's mapping of the published value.
Nothing is estimated, interpolated or taken from another evaluation (R-P.12).

021 FR-021.33 (P44, P66): when miWorkbench flags rows whose text it truncated, :func:`select_served_rows`
(pure, called once by :func:`join`) keeps the untruncated rows and the AUROC miWorkbench states over
them; runtime-detected, so a miWorkbench without the flag takes today's path.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pyarrow.parquet as pq
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...clients import miworkbench_client as mc
from ...models.detector_send import DetectorSend, DetectorSendStep
from ...models.publish import Publish, PublishBuild
from ..publishing import build_service
from .label_rules import target_for, value_key

#: miWorkbench's mapped label of a kept row (035 FTDD section 5.4: ``labels.npy`` int8).
LABEL_OF_TARGET = {"positive": 1, "negative": 0}


@dataclass(frozen=True)
class Refusal:
    reason: str


@dataclass(frozen=True)
class Joined:
    scores: list[float]
    labels: list[bool]
    groups: list[Any]
    keys: list[tuple[str, int]]
    reported_auroc: float | None
    #: 021 FR-021.33: rows miWorkbench flagged as truncated and left out of the join.
    excluded_truncated: int = 0


def fetch(
    client: mc.MiWorkbenchClient, probe_id: str, evaluation_id: str
) -> dict[str, Any] | Refusal:
    """All pages of one evaluation's scores, or miWorkbench's refusal in its own words."""
    try:
        return client.iter_evaluation_scores(probe_id, evaluation_id)
    except mc.MiWorkbenchPositionsNotRecorded as exc:
        detail = exc.detail if isinstance(exc.detail, Mapping) else {}
        return Refusal(
            f"miWorkbench: {detail.get('message') or exc.message} (re-evaluate with "
            f"{detail.get('evaluate') or 'the evaluate call'})"
        )
    except mc.MiWorkbenchScoresRefused as exc:
        return Refusal(exc.message)
    except mc.MiWorkbenchRefused as exc:
        return Refusal(f"miWorkbench refused the scores (HTTP {exc.status}): {exc.detail}")


def _download_step(session: Session, send_id: str, role_id: str) -> DetectorSendStep | None:
    for step in session.execute(
        select(DetectorSendStep).where(
            DetectorSendStep.send_id == send_id, DetectorSendStep.step == "download"
        )
    ).scalars():
        if role_id in (step.role_ids or []):
            return step
    return None


def _build_file(
    session: Session, send: DetectorSend, version_id: str, split: str
) -> tuple[Any, str] | Refusal:
    """The published split file at the send's publish of ``version_id`` (D50): publish step →
    ``Publish`` → ``PublishBuild`` → its file for ``split`` → ``build_file_path``."""
    step = session.execute(
        select(DetectorSendStep).where(
            DetectorSendStep.send_id == send.id,
            DetectorSendStep.step == "publish",
            DetectorSendStep.unit_key == version_id,
        )
    ).scalar_one_or_none()
    if step is None or step.publish_id is None:
        return Refusal("this send recorded no publish of the role's version")
    publish = session.get(Publish, step.publish_id)
    build = session.get(PublishBuild, publish.build_id) if publish is not None else None
    if build is None or not build.files:
        return Refusal(f"the published build of publish {step.publish_id} is gone")
    entry = next((f for f in build.files if f["name"] == split), None)
    if entry is None:
        return Refusal(f"the published build {build.id} has no split {split!r}")
    path = build_service.build_file_path(build, str(entry["path"]))
    if not path.is_file():
        return Refusal(f"the published build file is gone ({build.id}/{entry['path']})")
    return path, build.id


#: 021 FR-021.33 (P44): miWorkbench's G5 answer flags rows whose text it truncated, and states the
#: AUROC over the untruncated rows. The flag logic lives here and only here (P66, R3B-3).
UNTRUNCATED_KEY = "reported_auroc_untruncated"


@dataclass(frozen=True)
class Selected:
    rows: list[Mapping[str, Any]]
    reported_auroc: float | None
    excluded_truncated: int


def select_served_rows(served: Mapping[str, Any]) -> Selected | Refusal:
    """Pure: the served rows to join, the AUROC to check them against, and how many truncated rows
    were left out. The edges, in order (R1B-X14):

    1. no row carries ``truncated``, or every value is null: today's path (a miWorkbench before
       its G5 change, or a set it never measured), with ``reported_auroc``;
    2. some rows carry a boolean and others none or null: refused ``truncated_flag_incomplete``;
    3. flagged and ``reported_auroc_untruncated`` is null: refused with miWorkbench's own
       ``untruncated_reason`` (``too_few_untruncated``, ``truncation_not_recorded`` or the
       evaluation's refusal; ``too_few_untruncated`` when it states none), naming the count kept;
    4. flagged and the key is absent: refused ``untruncated_auroc_missing``;
    5. flagged with a value: the untruncated rows, checked against it.

    Never the all-rows AUROC against filtered rows."""
    rows = list(served["rows"])
    flags = [row.get("truncated") for row in rows]
    flagged = any(flag is not None for flag in flags)
    if not flagged:
        auroc = served["reported_auroc"]
        return Selected(rows, None if auroc is None else float(auroc), 0)
    if any(flag is None for flag in flags):
        missing = sum(1 for flag in flags if flag is None)
        return Refusal(
            f"truncated_flag_incomplete: miWorkbench flagged truncation on {len(rows) - missing} "
            f"row(s) and not on {missing}, so its untruncated AUROC cannot be matched to a known "
            "row set"
        )
    kept = [row for row in rows if row.get("truncated") is False]
    excluded = len(rows) - len(kept)
    if UNTRUNCATED_KEY not in served:
        return Refusal(
            "untruncated_auroc_missing: miWorkbench flagged truncated rows but stated no AUROC "
            "over the untruncated ones; its all-rows AUROC measures other rows"
        )
    untruncated = served[UNTRUNCATED_KEY]
    if untruncated is None:
        # miWorkbench states why (its G5 `served_untruncated_auroc`); never relabel its reason
        reason = served.get("untruncated_reason") or "too_few_untruncated"
        return Refusal(
            f"{reason}: miWorkbench states no AUROC over the {len(kept)} untruncated "
            f"row(s) ({excluded} truncated row(s) left out)"
        )
    return Selected(kept, float(untruncated), excluded)


def join_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    keys: Sequence[Any],
    occurrences: Sequence[Any],
    values: Sequence[Any],
    pairs: Sequence[Any] | None,
    mapping: Mapping[str, str],
) -> tuple[list[float], list[bool], list[Any], list[tuple[str, int]]] | Refusal:
    """Pure: each served row joined to the published row AT ITS POSITION (never its index in the
    response). Refuses an out-of-range position and a label that disagrees with ``mapping``."""
    scores: list[float] = []
    labels: list[bool] = []
    groups: list[Any] = []
    joined_keys: list[tuple[str, int]] = []
    for row in rows:
        position = int(row["position"])
        if not 0 <= position < len(keys):
            return Refusal(
                f"miWorkbench served position {position}; the published split has {len(keys)} rows"
            )
        target = target_for(value_key(values[position]), mapping)
        expected = LABEL_OF_TARGET.get(str(target))
        if expected is None or int(row["label"]) != expected:
            return Refusal(
                f"miWorkbench's label {row['label']} for position {position} (row "
                f"{keys[position]}) disagrees with this role's mapping of "
                f"{values[position]!r} ({target or 'unmapped'})"
            )
        scores.append(float(row["score"]))
        labels.append(expected == 1)
        groups.append(pairs[position] if pairs is not None else None)
        joined_keys.append((str(keys[position]), int(occurrences[position])))
    return scores, labels, groups, joined_keys


def join(
    session: Session,
    send_id: str,
    view_id: str,
    role: Mapping[str, Any],
    served: Mapping[str, Any],
) -> Joined | Refusal:
    """Map ``served`` (one evaluation, all pages) onto this role's published rows by position.

    ``role`` is ``results_service.SendView.role_by_view[view_id]``: ``role_id``, ``version_id``,
    ``split``, ``label_column``, ``label_mapping``, ``pair_column``."""
    send = session.get(DetectorSend, send_id)
    assert send is not None
    if str(served["dataset_id"]) != view_id:
        return Refusal(f"miWorkbench's scores are for view {served['dataset_id']}, not {view_id}")
    download = _download_step(session, send_id, str(role["role_id"]))
    if download is None or download.commit is None:
        return Refusal("this send recorded no download of the role's split")
    commit = download.commit
    got = served["revision"]
    if got != commit:
        return Refusal(
            f"miWorkbench scored the split at {got or mc.NOT_RECORDED}; this send published "
            f"{commit}, so positions cannot be mapped to its rows"
        )
    if str(served["source_dataset_id"]) != str(download.mistudio_dataset_id):
        return Refusal(
            f"miWorkbench scored dataset {served['source_dataset_id']}; this send downloaded "
            f"{download.mistudio_dataset_id}"
        )
    located = _build_file(session, send, str(role["version_id"]), str(role["split"]))
    if isinstance(located, Refusal):
        return located
    path, _build_id = located
    label_column = str(role["label_column"])
    pair_column = role["pair_column"]
    columns = ["_dw_row_key", "_dw_occurrence", label_column] + (
        [str(pair_column)] if pair_column else []
    )
    selected = select_served_rows(served)
    if isinstance(selected, Refusal):
        return selected
    table = pq.read_table(path, columns=columns)
    joined = join_rows(
        selected.rows,
        keys=table.column("_dw_row_key").to_pylist(),
        occurrences=table.column("_dw_occurrence").to_pylist(),
        values=table.column(label_column).to_pylist(),
        pairs=table.column(str(pair_column)).to_pylist() if pair_column else None,
        mapping=dict(role["label_mapping"]),
    )
    if isinstance(joined, Refusal):
        return joined
    scores, labels, groups, joined_keys = joined
    return Joined(
        scores, labels, groups, joined_keys, selected.reported_auroc, selected.excluded_truncated
    )
