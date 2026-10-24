"""``trl_validate`` in FILTER mode: drop rows that break TRL's column contract (FR-004.22, 004.23).

Each dropped row names the rule and column (``trl_invalid``). The context-window rule is
``not_checked`` in v1 (T-15) and never drops or passes a row; check mode over a whole version is
``services/curation/trl_service.check_version`` (feature 008's export gate).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pyarrow as pa

from ....services.curation import trl_rules
from ...context import RunContext
from ...protocol import OperatorResult
from .common import manifest, refuse


class TrlValidate:
    manifest = manifest(
        "trl_validate",
        "filter",
        "Drops rows that break the TRL trainer's column contract for the target type (roles "
        "alternate, no empty turn, chosen and rejected present, PRM lengths equal).",
        params={
            "target_type": {
                "type": "string",
                "enum": list(trl_rules.TARGET_TYPES),
                "title": "Target type",
            }
        },
        required=("target_type",),
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        target = str(params["target_type"])
        variant, missing = trl_rules.formable(target, batch.schema.names)
        if variant is None:
            raise refuse(
                "trl_invalid",
                f"The input has no column {missing[0]!r}, which {target} needs.",
                {"missing": missing},
            )
        if batch.num_rows == 0:
            return OperatorResult(output=batch)
        rules = trl_rules.rules_for(target, variant)
        keep, events = [], []
        for i, row in enumerate(batch.to_pylist()):
            failed = [
                (rule, outcome)
                for rule, outcome in trl_rules.check_row(rules, row)
                if outcome.status == "fail"
            ]
            if failed:
                rule, outcome = failed[0]
                events.append(
                    ctx.drop(
                        (row["_dw_row_key"], row["_dw_occurrence"]),
                        "trl_invalid",
                        f"{rule.id}: {outcome.message}",
                        rule.id,
                        text=",".join(rule.columns),
                    )
                )
            else:
                keep.append(i)
        return OperatorResult(output=batch.take(pa.array(keep, pa.int64())), events=events)
