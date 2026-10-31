"""``profile``: the profile as a report step (FR-004.6; the guided Profile step's sample run).

The same ``profile_service.build_profile`` the API uses; in a preview every figure carries
``sample: true`` (FR-004.5). A report touches no row.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pyarrow as pa

from ....services.curation.profile_service import build_profile
from ...context import RunContext
from ...protocol import OperatorResult
from .common import manifest, read_all_or_empty


class Profile:
    manifest = manifest(
        "profile",
        "report",
        "Counts, nulls, length and turn histograms, duplicates and clusters, each with its scale "
        "and sample size; figures that cannot be computed say why.",
        scope="dataset",
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        table = read_all_or_empty(ctx, batch)
        report = build_profile(table, ctx.column_roles, sample=ctx.sample, seed=ctx.step_seed)
        return OperatorResult(output=table, report=report)
