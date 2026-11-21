"""``check_carrier_ids@1``: refuse a build whose rows of two generation runs share a carrier id.

The merge step of a behaviour's pipeline. ``slot_siblings@1`` and ``split@1`` refuse the same thing, but
they run after the second-opinion labels are paid for; this runs on the merged rows, before them (dry
run, 2026-11-01: 2,659 rows). It changes no row. ``carrier_plan@1.1.0`` makes the collision impossible
for new plans; this is what stands in front of plans pinned to @1.0.0 and of rows built before it.
Its manifest marks it a check, so ``dataworks_run_version_check`` runs it over a whole built version.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pyarrow as pa

from ...context import RunContext
from ...errors import StepFailed
from ...protocol import OperatorResult
from ..curation.common import (
    CARRIER_ID,
    GENERATION_RUN,
    manifest,
    read_all,
    refuse_carrier_collisions,
)
from .common import NEXT_VALIDATE


class CheckCarrierIds:
    manifest = manifest(
        "check_carrier_ids",
        "mapper",
        "Refuses the build when rows of two generation runs share a carrier id they were not planned "
        "to share, so a merge fails before anything is labelled. Changes no row.",
        scope="dataset",
        check=True,
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        table = read_all(ctx)
        # Never passes by absence: without either column the check below would see nothing to refuse.
        missing = [c for c in (CARRIER_ID, GENERATION_RUN) if c not in table.schema.names]
        if missing:
            raise StepFailed(
                "carrier_columns_missing",
                f"This step reads {missing}, which the input does not have: it belongs after a "
                "merge of generated carrier rows, before those columns are dropped.",
                {"missing": missing, "next": NEXT_VALIDATE},
            )
        refuse_carrier_collisions(table, [CARRIER_ID])
        return OperatorResult(output=table, report={"checked_rows": table.num_rows})
