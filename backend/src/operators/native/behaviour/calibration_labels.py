"""``calibration_labels@1.0.0``: a test set from a calibration set's human answers (ENH-006 D116 G6).

The probe's other scores use the plan's labels, so they cannot show where those labels are wrong. This
keeps only the rows a calibration set labelled and writes each one's human label, so a person's answers
grade the probe. It reads the set by row key, so a recipe carries no carrier ids; a set is immutable, so
a build stays reproducible from the set's id alone.

``exclude_versions`` names the training splits a probe compared on this set was fitted on. Any row that
shares a row key, ``carrier_id`` or ``pair_id`` with one is dropped: each rebuild moves rows between
splits, so a row clear of one training set can sit in the next (a dry run's set lost 5 of 172 rows this
way). A masked row stays, and a later ``rule_label`` separates it.

Reading the set and the excluded splits opens a short read-only session: a calibration set is a
reference the step cannot carry in its parameters, like ``contamination_check``'s pinned benchmark
(the recorded deviation from 003's "an operator never opens a session").
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import select

from ....core.database import sync_session_factory
from ....core.storage import resolve_under_data_dir
from ....models.calibration import CalibrationSet, CalibrationSetLabel
from ....models.enums import VersionState
from ....models.version import Version
from ....rules.next_step import next_for
from ...context import RunContext
from ...errors import StepFailed
from ...manifest import ColumnSpec
from ...protocol import OperatorResult
from ..curation.common import CARRIER_ID, PAIR_ID, manifest, read_all_or_empty
from ..identifiers.common import append_outputs

NOT_LABELLED = "not_in_calibration_set"
SHARES_ROW = "shares_row_with_excluded_split"
SHARES_GROUP = "shares_group_with_excluded_split"
GROUP_COLUMNS = (CARRIER_ID, PAIR_ID)


@dataclass
class _Excluded:
    keys: set[str] = field(default_factory=set)
    groups: set[str] = field(default_factory=set)
    #: Whether every excluded split had a group column to compare against.
    every_split_has_groups: bool = True


def _labels(calibration_set_id: str) -> tuple[dict[str, str], list[str]]:
    with sync_session_factory()() as session:
        found = session.get(CalibrationSet, calibration_set_id)
        if found is None:
            raise StepFailed(
                "calibration_set_not_found",
                f"No calibration set {calibration_set_id}.",
                {
                    "calibration_set_id": calibration_set_id,
                    "next": next_for(
                        "dataworks_list_calibration_sets", {}, "find the calibration set's id"
                    ),
                },
            )
        rows = session.execute(
            select(CalibrationSetLabel.row_key, CalibrationSetLabel.human_label).where(
                CalibrationSetLabel.calibration_set_id == calibration_set_id,
                CalibrationSetLabel.human_label.is_not(None),
            )
        ).all()
        return {str(key): str(label) for key, label in rows}, list(found.label_set)


def _exclude_list(params: Mapping[str, Any]) -> list[dict[str, str]]:
    """``exclude_versions``, parsed. A malformed one is refused: dropping it silently would keep
    rows the operator asked to leave out."""
    raw = params.get("exclude_versions")
    if raw is None:
        return []
    try:
        items = json.loads(str(raw))
    except ValueError:
        items = None
    if not isinstance(items, list) or not all(
        isinstance(i, dict) and set(i) == {"version_id", "split"} and all(i.values()) for i in items
    ):
        raise StepFailed(
            "params_invalid",
            'exclude_versions is JSON text: a list of {"version_id": ..., "split": ...}.',
            {"errors": [{"pointer": "/exclude_versions", "message": "not that shape"}]},
        )
    return [{"version_id": str(i["version_id"]), "split": str(i["split"])} for i in items]


def _excluded(items: list[dict[str, str]]) -> _Excluded:
    out = _Excluded()
    with sync_session_factory()() as session:
        for item in items:
            version_id, split = item["version_id"], item["split"]
            version = session.get(Version, version_id)
            chosen = (
                next((s for s in version.splits if s["name"] == split), None)
                if version is not None and version.state == VersionState.COMPLETED
                else None
            )
            if chosen is None:
                raise StepFailed(
                    "exclude_version_unavailable",
                    f"Version {version_id} has no readable split {split!r} to leave out. Its rows "
                    "could not be left out, so this build stops rather than keep them.",
                    {
                        "version_id": version_id,
                        "split": split,
                        "next": next_for(
                            "dataworks_get_version",
                            {"version_id": version_id},
                            "read the version's state and its splits",
                        ),
                    },
                )
            path = resolve_under_data_dir(chosen["path"])
            names = set(pq.read_schema(path).names)
            columns = [c for c in GROUP_COLUMNS if c in names]
            if not columns:
                out.every_split_has_groups = False
            table = pq.read_table(path, columns=["_dw_row_key", *columns])
            out.keys.update(str(k) for k in table.column("_dw_row_key").to_pylist())
            for column in columns:
                out.groups.update(str(v) for v in table.column(column).to_pylist() if v is not None)
    return out


class CalibrationLabels:
    manifest = manifest(
        "calibration_labels",
        "labeler",
        "Keeps only the rows a calibration set labelled and writes each row's human label, so a "
        "person's answers grade a probe. Drops any row sharing a row key, carrier_id or pair_id with "
        "a named training split. Reads the set by row key; a set is immutable, so a build reproduces.",
        scope="dataset",
        params={
            "calibration_set_id": {
                "type": "string",
                "minLength": 1,
                "title": "Calibration set",
            },
            "output_column": {
                "type": "string",
                "minLength": 1,
                "pattern": "^[A-Za-z0-9_]{1,64}$",
                "title": "Output column",
            },
            # JSON text: the parameter subset has no array of objects (``slot_siblings``' variants).
            "exclude_versions": {
                "type": "string",
                "minLength": 2,
                "title": "Training splits to stay clear of (JSON)",
                "x-widget": "textarea",
                "x-hint": '[{"version_id": "<id>", "split": "train"}]. Name every training version '
                "a probe compared on this set was fitted on.",
            },
        },
        required=("calibration_set_id", "output_column"),
        output_columns=(ColumnSpec(name="label", type="string", role="metadata"),),
    )

    def columns_for(self, params: Mapping[str, Any]) -> tuple[list[str], list[str], list[str]]:
        return [], [str(params.get("output_column") or "label")], []

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        table = read_all_or_empty(ctx, batch)
        answers, label_set = _labels(str(params["calibration_set_id"]))
        exclude_list = _exclude_list(params)
        excluded = _excluded(exclude_list)
        names = table.schema.names
        input_groups = [c for c in GROUP_COLUMNS if c in names]
        if exclude_list and input_groups and not excluded.every_split_has_groups:
            raise StepFailed(
                "exclude_version_no_group_columns",
                f"The rows carry {input_groups}, and a split named in exclude_versions carries "
                "neither carrier_id nor pair_id, so a row sharing a carrier with it could not be "
                "seen. Name a split that still holds those columns.",
                {
                    "input_columns": input_groups,
                    "next": next_for(
                        "dataworks_list_versions",
                        {},
                        "find the version whose training split still holds the group columns",
                    ),
                },
            )
        keys = table.column("_dw_row_key").to_pylist()
        occurrences = table.column("_dw_occurrence").to_pylist()
        groups = {c: table.column(c).to_pylist() for c in input_groups}
        keep: list[int] = []
        values: list[str] = []
        events = []
        left: Counter[str] = Counter()
        kept: Counter[str] = Counter()
        seen: set[str] = set()
        for i, key in enumerate(keys):
            pair = (key, occurrences[i])
            answer = answers.get(key)
            if answer is None:
                left[NOT_LABELLED] += 1
                events.append(
                    ctx.drop(
                        pair,
                        NOT_LABELLED,
                        "The calibration set holds no label for this row.",
                        "calibration_set",
                        text=str(params["calibration_set_id"]),
                    )
                )
                continue
            seen.add(key)
            shared = next(
                (
                    c
                    for c in input_groups
                    if groups[c][i] is not None and str(groups[c][i]) in excluded.groups
                ),
                None,
            )
            if key in excluded.keys:
                left[SHARES_ROW] += 1
                events.append(
                    ctx.drop(
                        pair, SHARES_ROW, "This row is in a named split.", "_dw_row_key", text=key
                    )
                )
            elif shared is not None:
                left[SHARES_GROUP] += 1
                events.append(
                    ctx.drop(
                        pair,
                        SHARES_GROUP,
                        f"Its {shared} {groups[shared][i]!r} is in a named split.",
                        shared,
                        text=str(groups[shared][i]),
                    )
                )
            else:
                keep.append(i)
                values.append(answer)
                kept[answer] += 1
        if not keep and not ctx.sample:
            # A sample may simply miss the set's rows; a whole input with none of them is the wrong
            # input, and an empty test set would publish as if it graded something.
            raise StepFailed(
                "calibration_set_rows_absent",
                f"None of the rows left holds a label from calibration set "
                f"{params['calibration_set_id']}: {dict(left)}. Build on the version the set's rows "
                "are in, and check what exclude_versions leaves.",
                {
                    "labelled_rows": len(answers),
                    "left_out": dict(left),
                    "next": next_for(
                        "dataworks_get_calibration_set",
                        {"set_id": str(params["calibration_set_id"])},
                        "read which version the set's rows came from",
                    ),
                },
            )
        name = str(params["output_column"])
        output = append_outputs(
            table.take(pa.array(keep, pa.int64())),
            [(name, pa.array(values, pa.string()))],
            self.manifest.ref_text,
        )
        report = {
            "calibration_set_id": str(params["calibration_set_id"]),
            "label_set": label_set,
            "labelled_rows": len(answers),
            "labelled_rows_not_in_input": len(answers) - len(seen),
            "kept": len(keep),
            "kept_by_label": dict(sorted(kept.items())),
            "left_out": dict(sorted(left.items())),
            "excluded_splits": len(exclude_list),
        }
        return OperatorResult(
            output=output, events=events, output_roles={name: "metadata"}, report=report
        )
