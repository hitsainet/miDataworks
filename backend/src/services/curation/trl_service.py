"""The TRL validator in CHECK mode over a whole version (FR-004.23; FR-008.26).

Feature 008 calls ``api.validate_trl`` before writing any export; its seam reads ``valid`` and
``failures``. ``valid`` is True only when every evaluated rule passed on every row and the version
can form the target's columns. Rules that could not be evaluated are listed in ``not_checked`` with
their reason and never counted as passes (the context-window rule, always, in v1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...core.storage import resolve_under_data_dir
from ..duck import connect, files_param
from . import trl_rules
from .errors import CurationError

FAILURE_SAMPLE = 20
BATCH_ROWS = 10_000


@dataclass
class TrlValidationResult:
    version_id: str
    target_type: str
    trl_version: str
    valid: bool
    #: ``[{rule, column, row_key, message}]``, the first ``FAILURE_SAMPLE``.
    failures: list[dict[str, Any]] = field(default_factory=list)
    failure_counts: dict[str, int] = field(default_factory=dict)
    not_checked: list[dict[str, str]] = field(default_factory=list)
    rows: int = 0
    variant: list[str] | None = None

    def model_dump(self, mode: str = "json") -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "target_type": self.target_type,
            "trl_version": self.trl_version,
            "valid": self.valid,
            "failures": self.failures,
            "failure_counts": self.failure_counts,
            "not_checked": self.not_checked,
            "rows": self.rows,
            "variant": self.variant,
        }


def require_target(target_type: str) -> str:
    if target_type not in trl_rules.TARGET_TYPES:
        raise CurationError(
            "unknown_target_type",
            f"{target_type!r} is not a TRL target type; choose one of "
            + ", ".join(trl_rules.TARGET_TYPES)
            + ".",
            {"target_types": list(trl_rules.TARGET_TYPES)},
        )
    return target_type


def check_version(version: Any, target_type: str) -> TrlValidationResult:
    target = require_target(target_type)
    files = [resolve_under_data_dir(s["path"]) for s in version.splits]
    pin = trl_rules.PRM_TRL_VERSION if target == "prm" else trl_rules.TRL_VERSION
    con = connect()
    try:
        columns = (
            [
                str(r[0])
                for r in con.execute(
                    "SELECT name FROM parquet_schema(?) WHERE name IS NOT NULL",
                    [files_param(files)],
                ).fetchall()
            ]
            if files
            else []
        )
        variant, missing = trl_rules.formable(target, columns)
        result = TrlValidationResult(str(version.id), target, pin, valid=False)
        if variant is None:
            result.failures.append(
                {
                    "rule": "required_columns",
                    "column": missing[0],
                    "row_key": None,
                    "message": f"the version has no column {missing[0]!r} for {target}",
                }
            )
            result.failure_counts["required_columns"] = 1
            return result
        result.variant = list(variant)
        rules = trl_rules.rules_for(target, variant)
        needed = sorted({c for r in rules for c in r.columns} | {"_dw_row_key"})
        select = ", ".join('"' + c.replace('"', '""') + '"' for c in needed if c in columns)
        reader = con.execute(
            f"SELECT {select} FROM read_parquet(?, union_by_name=true)",  # noqa: S608
            [files_param(files)],
        ).to_arrow_reader(BATCH_ROWS)
        noted: set[str] = set()
        for batch in reader:
            for row in batch.to_pylist():
                result.rows += 1
                for rule, outcome in trl_rules.check_row(rules, row):
                    if outcome.status == "fail":
                        result.failure_counts[rule.id] = result.failure_counts.get(rule.id, 0) + 1
                        if len(result.failures) < FAILURE_SAMPLE:
                            result.failures.append(
                                {
                                    "rule": rule.id,
                                    "column": ",".join(rule.columns),
                                    "row_key": row.get("_dw_row_key"),
                                    "message": outcome.message,
                                }
                            )
                    elif outcome.status == "not_checked" and rule.id not in noted:
                        noted.add(rule.id)
                        result.not_checked.append({"rule": rule.id, "reason": outcome.message})
        result.valid = not result.failure_counts
        return result
    finally:
        con.close()
