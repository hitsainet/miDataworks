"""Category ``review``: feature 006's review queues, decisions and audits (006 FR-006.35,
FR-006.24; P-10; 010 FTID section 3.7).

An agent may ACCEPT or FLAG a row and nothing else (P-10): ``dataworks_review_decide`` offers only
those two, and REST refuses an agent override or reject with 403 even if another caller tried.
Agent decisions are recorded with the token's identity and never count as human labels or toward
audit completion. The miForge candidate and read-back routes have no tool: they are the
application API for miForge (R-03.41), not UI actions.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from ..client import DataworksClient
from ..context import FILES, ID_PATTERN, READ, Limit, Page, ToolContext
from ..health_gate import gated_call

QueueId = Annotated[str, Field(description="Review queue ID, rq_...", pattern=ID_PATTERN)]
ItemId = Annotated[str, Field(description="Review item ID, ri_...", pattern=ID_PATTERN)]
VersionId = Annotated[str, Field(description="Version ID.", pattern=ID_PATTERN)]


def register(mcp: MCPServer, client: DataworksClient, ctx: ToolContext) -> None:
    gate = ctx.gate

    @mcp.tool()
    async def dataworks_preview_calibration_mapping(
        version_id: VersionId,
        question: Annotated[
            str, Field(description="Exact question text.", min_length=1, max_length=4096)
        ],
        label_set: Annotated[
            list[str], Field(description="Labels, positive first.", min_length=2, max_length=64)
        ],
        mapping: Annotated[
            dict[str, Any], Field(description="dw.calibration-mapping/v1 document.")
        ],
    ) -> Any:
        """What a calibration mapping yields on a version (counts, sorted-ratings warning, sample
        rows). Writes nothing."""
        body = {
            "version_id": version_id,
            "question": question,
            "label_set": label_set,
            "mapping": mapping,
        }
        return await gated_call(
            gate, FILES, lambda: client.post("/calibration-sets/preview", json_body=body)
        )

    @mcp.tool()
    async def dataworks_list_review_queues(
        kind: Annotated[
            Literal["label_review", "calibration_labeling", "audit", "external"] | None,
            Field(description="Only queues of this kind."),
        ] = None,
        version_id: Annotated[
            str | None, Field(description="Only queues on this version.", pattern=ID_PATTERN)
        ] = None,
        page: Page = 1,
        limit: Limit = 50,
    ) -> Any:
        """Review queues with kind, size, progress and creator."""
        return await gated_call(
            gate,
            READ,
            lambda: client.get(
                "/review-queues", kind=kind, version_id=version_id, page=page, limit=limit
            ),
        )

    @mcp.tool()
    async def dataworks_create_review_queue(
        kind: Annotated[
            Literal["label_review", "calibration_labeling"],
            Field(
                description="label_review: a run's labels; calibration_labeling: rows for people to label."
            ),
        ],
        label_run_id: Annotated[
            str | None,
            Field(
                description="The label run (label_review), or strata for calibration labeling.",
                pattern=ID_PATTERN,
            ),
        ] = None,
        row_keys: Annotated[
            list[str] | None,
            Field(
                description="Explicit row keys (label_review), e.g. rows two labelers disagree on.",
                max_length=5000,
            ),
        ] = None,
        version_id: Annotated[
            str | None, Field(description="Version (calibration_labeling).", pattern=ID_PATTERN)
        ] = None,
        question: Annotated[
            str | None, Field(description="Question (calibration_labeling).", max_length=4096)
        ] = None,
        label_set: Annotated[
            list[str] | None, Field(description="Labels, positive first (calibration_labeling).")
        ] = None,
        size: Annotated[int | None, Field(description="Rows to sample.", ge=1, le=5000)] = None,
    ) -> Any:
        """Create a label-review or calibration-labeling queue with its sampled items. Agents
        cannot create external queues (an application's) or show model output to labelers."""
        body = {
            k: v
            for k, v in {
                "kind": kind,
                "label_run_id": label_run_id,
                "row_keys": row_keys,
                "version_id": version_id,
                "question": question,
                "label_set": label_set,
                "size": size,
            }.items()
            if v is not None
        }
        return await gated_call(gate, FILES, lambda: client.post("/review-queues", json_body=body))

    @mcp.tool()
    async def dataworks_get_review_queue(queue_id: QueueId) -> Any:
        """One review queue with its progress."""
        return await gated_call(gate, READ, lambda: client.get(f"/review-queues/{queue_id}"))

    @mcp.tool()
    async def dataworks_get_review_rows(
        queue_id: QueueId,
        decided: Annotated[
            bool | None, Field(description="Only decided (true) or undecided (false) rows.")
        ] = None,
        page: Page = 1,
        limit: Limit = 50,
    ) -> Any:
        """A page of a queue's items with row text and the latest decision. Model output is
        withheld on calibration-labeling queues that hide it."""
        return await gated_call(
            gate,
            FILES,
            lambda: client.get(
                f"/review-queues/{queue_id}/items", decided=decided, page=page, limit=limit
            ),
        )

    @mcp.tool()
    async def dataworks_review_decide(
        item_id: ItemId,
        decision: Annotated[
            Literal["accept", "flag"], Field(description="accept or flag only (P-10).")
        ],
        reason: Annotated[
            str | None, Field(description="Required to flag; why.", max_length=4000)
        ] = None,
    ) -> Any:
        """Accept the model's label or flag the row for the operator. Agents cannot override or
        reject (P-10); an agent decision never becomes a human label."""
        body = {k: v for k, v in {"decision": decision, "reason": reason}.items() if v is not None}
        return await gated_call(
            gate, READ, lambda: client.post(f"/review-items/{item_id}/decisions", json_body=body)
        )

    @mcp.tool()
    async def dataworks_get_review_decisions(item_id: ItemId) -> Any:
        """An item's decision history, oldest first, with who decided and why."""
        return await gated_call(
            gate, READ, lambda: client.get(f"/review-items/{item_id}/decisions")
        )

    @mcp.tool()
    async def dataworks_draw_audit(
        version_id: VersionId,
        size: Annotated[int, Field(description="Rows to sample, 50 to 100.", ge=50, le=100)] = 100,
        label_run_id: Annotated[
            str | None,
            Field(
                description="Run whose labels and bands stratify the sample.", pattern=ID_PATTERN
            ),
        ] = None,
    ) -> Any:
        """Draw a version's audit sample (supersedes one in progress). Only the OPERATOR's
        decisions complete it; a public push needs it complete."""
        body = {
            k: v for k, v in {"size": size, "label_run_id": label_run_id}.items() if v is not None
        }
        return await gated_call(
            gate, FILES, lambda: client.post(f"/versions/{version_id}/audit", json_body=body)
        )

    @mcp.tool()
    async def dataworks_get_audit_status(version_id: VersionId) -> Any:
        """A version's audit: none, in_progress or complete, with its result."""
        return await gated_call(gate, READ, lambda: client.get(f"/versions/{version_id}/audit"))
