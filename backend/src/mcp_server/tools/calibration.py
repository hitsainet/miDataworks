"""Category ``calibration``: feature 006's calibration sets, records, status and gate targets
(006 FR-006.35, FR-006.43; 010 FTID section 3.7).

AUROC is the area under the receiver operating characteristic curve; CI a confidence interval.
``dataworks_set_calibration_target`` waits for the operator (``gate_target_write``, S3-08): the REST
route stores the request with the old target, the new one and every verdict that would change, and
writes nothing until it is approved. Every other tool runs at once; the calibration itself is a job
that reads labels and calls no model.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from ..client import DataworksClient
from ..context import ID_PATTERN, JOB, READ, Limit, Page, ToolContext
from ..health_gate import gated_call

Hash = Annotated[
    str, Field(description="SHA-256, 64 lowercase hex characters.", pattern=r"^[0-9a-f]{64}$")
]
SetId = Annotated[str, Field(description="Calibration set ID, cs_...", pattern=ID_PATTERN)]
RecordId = Annotated[str, Field(description="Calibration record ID, cr_...", pattern=ID_PATTERN)]
Question = Annotated[
    str,
    Field(
        description="The exact question text the labeler is asked.", min_length=1, max_length=4096
    ),
]


def register(mcp: MCPServer, client: DataworksClient, ctx: ToolContext) -> None:
    gate = ctx.gate

    @mcp.tool()
    async def dataworks_import_calibration_set(
        version_id: Annotated[
            str, Field(description="Imported version holding human labels.", pattern=ID_PATTERN)
        ],
        question: Question,
        label_set: Annotated[
            list[str], Field(description="Labels, positive first.", min_length=2, max_length=64)
        ],
        mapping: Annotated[
            dict[str, Any],
            Field(
                description="dw.calibration-mapping/v1: human_label rule, optional ratings, group, strata, reference."
            ),
        ],
    ) -> Any:
        """Create a calibration set from an imported version's human-label column (FR-006.2 b).
        Preview the mapping first with dataworks_preview_calibration_mapping."""
        body = {
            "version_id": version_id,
            "question": question,
            "label_set": label_set,
            "mapping": mapping,
        }
        return await gated_call(
            gate, READ, lambda: client.post("/calibration-sets/import", json_body=body)
        )

    @mcp.tool()
    async def dataworks_build_calibration_set(
        queue_id: Annotated[
            str, Field(description="Calibration-labeling queue ID, rq_...", pattern=ID_PATTERN)
        ],
    ) -> Any:
        """Create a calibration set from a calibration-labeling queue. Only the OPERATOR's
        decisions become human labels; agent decisions never do (FR-006.24)."""
        return await gated_call(
            gate,
            READ,
            lambda: client.post("/calibration-sets/from-review", json_body={"queue_id": queue_id}),
        )

    @mcp.tool()
    async def dataworks_list_calibration_sets(
        version_id: Annotated[
            str | None, Field(description="Only sets on this version.", pattern=ID_PATTERN)
        ] = None,
        page: Page = 1,
        limit: Limit = 50,
    ) -> Any:
        """Calibration sets with their counts, licence class and provenance."""
        return await gated_call(
            gate,
            READ,
            lambda: client.get("/calibration-sets", version_id=version_id, page=page, limit=limit),
        )

    @mcp.tool()
    async def dataworks_get_calibration_set(set_id: SetId) -> Any:
        """One calibration set. Its rows are never shipped by any export (P-14)."""
        return await gated_call(gate, READ, lambda: client.get(f"/calibration-sets/{set_id}"))

    @mcp.tool()
    async def dataworks_compute_calibration(
        label_run_id: Annotated[
            str,
            Field(description="A completed label run on the set's version.", pattern=ID_PATTERN),
        ],
        calibration_set_id: SetId,
    ) -> Any:
        """Start a calibration job (AUROC with its 95% CI, the held-out rater ceiling, pairs,
        reliability, checks and the gate verdict). Answers 202 with the job ID; follow it with
        dataworks_job_status."""
        body = {"label_run_id": label_run_id, "calibration_set_id": calibration_set_id}
        return await gated_call(
            gate, JOB, lambda: client.post("/calibration-records", json_body=body)
        )

    @mcp.tool()
    async def dataworks_list_calibration_records(
        labeler: Annotated[Hash | None, Field(description="Labeler identity hash.")] = None,
        question_hash: Annotated[
            Hash | None, Field(description="SHA-256 of the question text.")
        ] = None,
        page: Page = 1,
        limit: Limit = 50,
    ) -> Any:
        """Calibration records, newest first, each with checks and verdict."""
        return await gated_call(
            gate,
            READ,
            lambda: client.get(
                "/calibration-records",
                labeler=labeler,
                question_hash=question_hash,
                page=page,
                limit=limit,
            ),
        )

    @mcp.tool()
    async def dataworks_get_calibration(record_id: RecordId) -> Any:
        """One calibration record: metrics with sample sizes, every check, the verdict and its
        numbers."""
        return await gated_call(gate, READ, lambda: client.get(f"/calibration-records/{record_id}"))

    @mcp.tool()
    async def dataworks_get_gate_verdict(
        labeler: Annotated[Hash | None, Field(description="Labeler identity hash.")] = None,
        fingerprint: Annotated[
            Hash | None, Field(description="Labeler fingerprint (resolved to its identity).")
        ] = None,
    ) -> Any:
        """The latest calibration verdict for a labeler (passes, fails, invalid, insufficient),
        or none_recorded. Pass exactly one of labeler or fingerprint."""
        return await gated_call(
            gate,
            READ,
            lambda: client.get("/calibration-status", labeler=labeler, fingerprint=fingerprint),
        )

    @mcp.tool()
    async def dataworks_get_calibration_targets(question_hash: Hash) -> Any:
        """The operator's gate target for a question and its history; none means the default
        (C3: CI lower bound 0.70 when no held-out rater ceiling exists)."""
        return await gated_call(
            gate, READ, lambda: client.get("/calibration-targets", question_hash=question_hash)
        )

    @mcp.tool()
    async def dataworks_set_calibration_target(
        question: Question,
        target: Annotated[
            float,
            Field(description="AUROC CI lower bound to reach, between 0.5 and 1.", gt=0.5, lt=1),
        ],
    ) -> Any:
        """Ask to change the gate target for a question. WAITS FOR THE OPERATOR
        (gate_target_write): the approval shows the old and new target and every labeler whose
        verdict would change; nothing changes until the operator approves."""
        body = {"question": question, "target": target}
        return await gated_call(
            gate, READ, lambda: client.put("/calibration-targets", json_body=body)
        )
