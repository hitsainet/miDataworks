"""``drop_leakage_pairs``: drop the rows a leakage report pairs across sides (PII run F-4).

D-4 refused the run's first detector set over 189 near-duplicate pairs and pointed to "feature
004's leakage view", which no MCP tool reaches; the run rebuilt everything to repair it. This
operator is the repair: it reads a leakage report's pairs file (``pairs_artefact``, pinned by its
``pairs_sha256``: a changed file refuses the step), and drops every row of THIS input that a pair
puts on ``drop_side`` against one of ``keep_sides``. ``dataworks_plan_leakage_repair`` fills the
parameters per role, so the evaluation side stays whole and the training side gives way.
Deterministic; a dropped row's reason names the side it crossed and the pair's kind.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from ....core.storage import resolve_under_data_dir
from ....services.identity import file_sha256
from ...context import RunContext
from ...protocol import OperatorResult
from .common import manifest, refuse

SHA256 = r"^[0-9a-f]{64}$"


def crossing_keys(
    pairs: list[dict[str, Any]], drop_side: str, keep_sides: set[str]
) -> dict[str, tuple[str, str]]:
    """``{row key on drop_side: (the other side, the pair's kind)}``."""
    out: dict[str, tuple[str, str]] = {}
    for p in pairs:
        for mine, theirs, key in (
            ("side_a", "side_b", "row_key_a"),
            ("side_b", "side_a", "row_key_b"),
        ):
            if p[mine] == drop_side and p[theirs] in keep_sides:
                out.setdefault(str(p[key]), (str(p[theirs]), str(p["kind"])))
    return out


class DropLeakagePairs:
    manifest = manifest(
        "drop_leakage_pairs",
        "filter",
        "Drops the rows a leakage report pairs across sides: every row of this input on "
        "drop_side that crosses one of keep_sides (exact, near or group pairs). Fill it with "
        "dataworks_plan_leakage_repair, which keeps evaluation roles whole.",
        params={
            "pairs_artefact": {
                "type": "string",
                "minLength": 1,
                "title": "Leakage pairs file",
                "x-hint": "runs/<report>/reports/leakage_pairs.parquet, from the leakage report.",
            },
            "pairs_sha256": {
                "type": "string",
                "pattern": SHA256,
                "title": "Pairs file SHA-256",
                "x-hint": "Pins the pairs: a changed file refuses the step.",
            },
            "drop_side": {"type": "string", "minLength": 1, "title": "Side to drop from"},
            "keep_sides": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "title": "Sides that stay whole",
            },
        },
        required=("pairs_artefact", "pairs_sha256", "drop_side", "keep_sides"),
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        if batch.num_rows == 0:
            return OperatorResult(output=batch)
        path = resolve_under_data_dir(str(params["pairs_artefact"]))
        if not path.is_file():
            raise refuse(
                "pairs_not_found",
                f"The leakage pairs file {params['pairs_artefact']} is gone. Run the leakage "
                "check again and plan the repair again.",
                {"pairs_artefact": params["pairs_artefact"]},
            )
        if file_sha256(path) != params["pairs_sha256"]:
            raise refuse(
                "pairs_changed",
                "The leakage pairs file no longer has the SHA-256 this step pins. Plan the repair "
                "again.",
                {"pairs_artefact": params["pairs_artefact"]},
            )
        drop = crossing_keys(
            pq.read_table(path).to_pylist(), str(params["drop_side"]), set(params["keep_sides"])
        )
        keys = batch.column("_dw_row_key").to_pylist()
        occ = batch.column("_dw_occurrence").to_pylist()
        keep, events = [], []
        for i, key in enumerate(keys):
            hit = drop.get(str(key))
            if hit is None:
                keep.append(i)
                continue
            side, kind = hit
            events.append(
                ctx.drop(
                    (key, occ[i]),
                    "leakage_pair",
                    f"pairs ({kind}) with a row of {side}, which stays whole",
                    "leakage_pair",
                    text=f"{kind}:{side}",
                )
            )
        return OperatorResult(output=batch.take(pa.array(keep, pa.int64())), events=events)
