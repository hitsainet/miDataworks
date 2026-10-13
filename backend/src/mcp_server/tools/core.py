"""Category ``core``: health, guidance, jobs and approvals (FR-010.19, .20, .21, .24).

Foundation routes: ``GET /api/health`` (3.7), the jobs routes (5.7), the approval routes (9.3).
``dataworks_howto`` makes no request; it is the server's own guidance.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from ..client import DataworksClient
from ..context import ID_PATTERN, READ, ToolContext
from ..health_gate import gated_call

HOWTO: dict[str, str] = {
    "overview": (
        "miDataworks makes data: it imports Hugging Face (HF) datasets and uploads as sources, "
        "builds immutable dataset versions from recipes (ordered operator steps), checks and "
        "publishes versions to the Hub, and exports training sets for TRL (Transformer "
        "Reinforcement Learning) and miForge. Workflow: dataworks_preview_hf_dataset -> "
        "dataworks_import_hf_dataset (a job) -> dataworks_create_dataset -> dataworks_save_recipe "
        "-> dataworks_build_version (a job) -> dataworks_get_version -> "
        "dataworks_build_version_files -> dataworks_run_publish_checks -> "
        "dataworks_publish_version (waits for the operator)."
    ),
    "results": (
        "Three result shapes. (1) The resource. (2) A pending approval: {approval_id, status: "
        "'pending', action, request_digest, expires_at}. The operator approves or rejects it in "
        "miDataworks; poll dataworks_get_approval_status. Agents cannot approve (403 "
        "AGENT_CANNOT_DECIDE). An approval expires after 24 hours. (3) {unavailable: <dependency>, "
        "reason}: the backend, PostgreSQL, Redis or the data volume is down. Report it; do not "
        "retry in a loop. A refusal is raised as a tool error carrying the backend's error code."
    ),
    "approvals": (
        "Seven actions wait for the operator when an agent asks: hub_push (any push to the Hub), "
        "agent_label_rows (over 5,000 agent-labelled rows per version per 24 hours), "
        "version_delete, millm_model_load (no route yet), secret_write (an HF token or an API "
        "key), source_annotate (a source's licence, terms or detection override) and "
        "gate_target_write (a calibration gate target or a probe reproduction link). The approval binds to the exact request: "
        "changing any field after approval needs a new approval."
    ),
    "jobs": (
        "Imports, builds, publish builds, checks, publishes and exports run as jobs. A route that "
        "starts one returns a job ID; poll dataworks_job_status. dataworks_cancel_job requests a "
        "cooperative stop: the job stops at its next checkpoint."
    ),
    "secrets": (
        "Tokens and API keys are taken only by dataworks_set_hf_token, dataworks_set_endpoint_key "
        "and the optional access_token of the HF preview and import tools. Each waits for the "
        "operator, is stored encrypted, and is never returned: reads show a mask."
    ),
}


def register(mcp: MCPServer, client: DataworksClient, ctx: ToolContext) -> None:
    @mcp.tool()
    async def dataworks_health() -> Any:
        """The backend's health: overall status and each dependency (PostgreSQL, Redis, the data
        volume, miLLM, miStudio) with a reason when it is down. Not gated: it is how you learn
        what is down. Returns {"unavailable": "backend", "reason": ...} when the backend itself
        cannot be reached."""

        async def call() -> Any:
            return await client.get("/api/health")

        return await gated_call(ctx.gate, ("backend",), call)

    @mcp.tool()
    async def dataworks_howto(
        topic: Annotated[
            Literal["overview", "results", "approvals", "jobs", "secrets"] | None,
            Field(description="One topic; omit for the list of topics with the overview."),
        ] = None,
    ) -> Any:
        """Guidance for agents: the workflow, the three result shapes (resource, pending approval,
        unavailable), the seven approval actions, jobs and secrets. Call this first. Makes no
        request to the backend."""
        if topic is None:
            return {"topics": sorted(HOWTO), "overview": HOWTO["overview"]}
        return {"topic": topic, "text": HOWTO[topic]}

    @mcp.tool()
    async def dataworks_list_jobs(
        state: Annotated[
            Literal["active", "failed", "finished"] | None,
            Field(description="Filter: active (queued or running), failed, or finished."),
        ] = None,
        kind: Annotated[
            str | None,
            Field(description="Filter by job kind, for example hf_import.", max_length=64),
        ] = None,
    ) -> Any:
        """List jobs (imports, builds, publishes, exports) with state and progress."""
        return await gated_call(ctx.gate, READ, lambda: client.get("/jobs", state=state, kind=kind))

    @mcp.tool()
    async def dataworks_job_status(
        job_id: Annotated[str, Field(description="Job ID, job_...", pattern=ID_PATTERN)],
    ) -> Any:
        """One job's state, progress, result reference and error."""
        return await gated_call(ctx.gate, READ, lambda: client.get(f"/jobs/{job_id}"))

    @mcp.tool()
    async def dataworks_cancel_job(
        job_id: Annotated[str, Field(description="Job ID, job_...", pattern=ID_PATTERN)],
        reason: Annotated[
            str, Field(description="Why the job is cancelled; recorded on the job.", max_length=500)
        ] = "Cancelled by an agent.",
    ) -> Any:
        """Request a cooperative stop of a running or queued job. The job stops at its next
        checkpoint; poll dataworks_job_status for the final state."""
        return await gated_call(
            ctx.gate,
            READ,
            lambda: client.post(f"/jobs/{job_id}/cancel", json_body={"reason": reason}),
        )

    @mcp.tool()
    async def dataworks_list_approvals(
        status: Annotated[
            Literal["pending", "executing", "executed", "failed", "rejected", "expired"] | None,
            Field(description="Filter by status; omit for every approval, newest first."),
        ] = None,
    ) -> Any:
        """List approval requests: action, who asked, status, expiry, and the result once run.
        Secret values are never included."""
        return await gated_call(ctx.gate, READ, lambda: client.get("/approvals", status=status))

    @mcp.tool()
    async def dataworks_get_approval_status(
        approval_id: Annotated[str, Field(description="Approval ID, apr_...", pattern=ID_PATTERN)],
    ) -> Any:
        """One approval: pending, executing, executed (with result_kind and result_id), failed
        (with error), rejected (with reason) or expired. Poll this after a tool returned a
        pending approval."""
        return await gated_call(ctx.gate, READ, lambda: client.get(f"/approvals/{approval_id}"))
