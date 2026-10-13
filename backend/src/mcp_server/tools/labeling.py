"""Category ``labeling``: endpoint roles (Foundation 8.3) and feature 005's label runs, decision
templates, rubrics, sampling and keep-share previews (FR-005.52, FR-005.36).

``dataworks_start_label_run`` waits for the operator when an agent's run would take the version's
agent total over 5,000 rows in 24 hours (``agent_label_rows``, P-07); the count is computed by the
REST plan, never taken from the caller. Template and rubric export/import and label-run cancel
have no tool (file transfer; an alias of ``dataworks_cancel_job``).
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from ..client import DataworksClient
from ..context import FILES, ID_PATTERN, JOB_FILES, READ, Limit, Page, ToolContext
from ..health_gate import gated_call

VersionId = Annotated[str, Field(description="Input version ID, ver_...", pattern=ID_PATTERN)]
LabelRole = Annotated[
    Literal["classifier", "judge", "probe", "features"],
    Field(
        description="Which labeler labels the rows: the classifier or judge endpoint role, "
        "'probe' for a probe-verdict run (a miLLM probe; name it with probe_id), or 'features' "
        "for a feature-tag run (an SAE attached in miLLM; describe it with sae_read)."
    ),
]
SaeRead = Annotated[
    dict[str, Any] | None,
    Field(
        description="Feature-tag runs: {top_k, positions: last|prompt|all, sae_id?, features?} "
        "- the attached SAE miLLM reads unsteered in scoring mode."
    ),
]
ProbeId = Annotated[
    str | None,
    Field(
        description="Probe-verdict runs: the probe's ID in miLLM (see dataworks_list_millm_probes).",
        max_length=64,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    ),
]
ProbeWindow = Annotated[
    Literal["all", "prompt", "response", "last_user"] | None,
    Field(description="Probe-verdict runs: the window miLLM reads (default all)."),
]
RetryReason = Annotated[
    str | None,
    Field(
        description="Probe-verdict runs: run the reproduction check again although the same check "
        "already failed against the same target, saying why (recorded on the run). Otherwise "
        "change the link, window, input form or model revision first.",
        min_length=1,
        max_length=2000,
    ),
]
TemplateId = Annotated[
    str | None, Field(description="Decision template ID (classifier runs).", pattern=ID_PATTERN)
]
RubricId = Annotated[str | None, Field(description="Rubric ID (judge runs).", pattern=ID_PATTERN)]
Question = Annotated[
    str | None, Field(description="The question the template or rubric asks.", max_length=4000)
]
FieldMap = Annotated[
    dict[str, str] | None,
    Field(description='Template placeholder -> row column, for example {"text": "body"}.'),
]
Threshold = Annotated[float | None, Field(description="Probability threshold, 0 to 1.", ge=0, le=1)]
RowFilter = Annotated[
    dict[str, str] | None,
    Field(description='Rows to label by origin: {"origin": "generated" | "source"}.'),
]
RunId = Annotated[str, Field(description="Label run ID, lr_...", pattern=ID_PATTERN)]

Label = Annotated[str | None, Field(description="Label name for this side.", max_length=200)]
SamplingArg = Annotated[
    dict[str, Any] | None,
    Field(description="Judge sampling: {temperature, seed, max_tokens}."),
]
Chunk = Annotated[int | None, Field(description="Rows per chunk.", ge=1, le=10_000)]
KeepShare = Annotated[
    str | None,
    Field(description="A keep-share job whose estimate the run used.", pattern=ID_PATTERN),
]
Transport = Annotated[
    Literal["single", "batch"], Field(description="single requests, or miLLM batch.")
]


def compact(body: dict[str, Any]) -> dict[str, Any]:
    """The body without unset fields, so the backend's own defaults apply."""
    return {k: v for k, v in body.items() if v is not None}


def _start_body(
    input_version_id: str,
    role: str,
    template_id: str | None,
    rubric_id: str | None,
    question: str | None,
    field_map: dict[str, str] | None,
    threshold_positive: float | None,
    threshold_negative: float | None,
    min_top_probability: float | None,
    positive_label: str | None,
    negative_label: str | None,
    sampling: dict[str, Any] | None,
    chunk_size: int | None,
    row_filter: dict[str, str] | None,
    keep_share_job_id: str | None,
    transport: str,
    probe_id: str | None = None,
    window: str | None = None,
    sae_read: dict[str, Any] | None = None,
    reproduction_retry_reason: str | None = None,
) -> dict[str, Any]:
    probe = None
    if probe_id is not None:
        probe = compact({"probe_id": probe_id, "window": window})
    return compact(
        {
            "input_version_id": input_version_id,
            "role": role,
            "template_id": template_id,
            "rubric_id": rubric_id,
            "question": question,
            "field_map": field_map,
            "threshold_positive": threshold_positive,
            "threshold_negative": threshold_negative,
            "min_top_probability": min_top_probability,
            "positive_label": positive_label,
            "negative_label": negative_label,
            "sampling": sampling,
            "chunk_size": chunk_size,
            "row_filter": row_filter,
            "keep_share_job_id": keep_share_job_id,
            "transport": transport,
            "probe": probe,
            "features": sae_read,
            "reproduction_retry_reason": reproduction_retry_reason,
        }
    )


def register(mcp: MCPServer, client: DataworksClient, ctx: ToolContext) -> None:
    gate = ctx.gate

    @mcp.tool()
    async def dataworks_list_endpoint_roles() -> Any:
        """The four endpoint roles (classifier, judge, generation, embeddings) with protocol, URL
        and model. Keys are masked."""
        return await gated_call(gate, READ, lambda: client.get("/endpoint-roles"))

    @mcp.tool()
    async def dataworks_fetch_models(
        role: Annotated[
            Literal["classifier", "judge", "generation", "embeddings"],
            Field(description="The endpoint role whose server to ask."),
        ],
        base_url: Annotated[
            str | None,
            Field(description="Try this URL instead of the saved one.", max_length=2048),
        ] = None,
    ) -> Any:
        """List the models the role's server serves (OpenAI /v1/models, or TEI /info)."""
        return await gated_call(
            gate, READ, lambda: client.get(f"/endpoint-roles/{role}/models", base_url=base_url)
        )

    # ------------------------------------------------------------ 005 templates and rubrics

    @mcp.tool()
    async def dataworks_list_templates() -> Any:
        """List decision templates (the prompt and answer tokens a classifier run uses)."""
        return await gated_call(gate, READ, lambda: client.get("/decision-templates"))

    @mcp.tool()
    async def dataworks_save_template(
        name: Annotated[str, Field(description="Template name.", min_length=1, max_length=200)],
        body: Annotated[dict[str, Any], Field(description="The template body (005 FR-005.6).")],
    ) -> Any:
        """Create a decision template. Templates are versioned and never edited in place."""
        return await gated_call(
            gate,
            READ,
            lambda: client.post("/decision-templates", json_body={"name": name, "body": body}),
        )

    @mcp.tool()
    async def dataworks_get_template(
        template_id: Annotated[str, Field(description="Template ID.", pattern=ID_PATTERN)],
    ) -> Any:
        """One decision template and its body."""
        return await gated_call(
            gate, READ, lambda: client.get(f"/decision-templates/{template_id}")
        )

    @mcp.tool()
    async def dataworks_clone_template(
        template_id: Annotated[str, Field(description="Template to copy.", pattern=ID_PATTERN)],
        body: Annotated[
            dict[str, Any] | None, Field(description="A changed body; omit to copy as is.")
        ] = None,
    ) -> Any:
        """Copy a decision template as a new version, optionally with a changed body."""
        return await gated_call(
            gate,
            READ,
            lambda: client.post(
                f"/decision-templates/{template_id}/clone", json_body={"body": body}
            ),
        )

    @mcp.tool()
    async def dataworks_list_rubrics() -> Any:
        """List rubrics (what a judge run scores against)."""
        return await gated_call(gate, READ, lambda: client.get("/rubrics"))

    @mcp.tool()
    async def dataworks_save_rubric(
        name: Annotated[str, Field(description="Rubric name.", min_length=1, max_length=200)],
        body: Annotated[dict[str, Any], Field(description="The rubric body (005 FR-005.9).")],
    ) -> Any:
        """Create a rubric. Rubrics are versioned and never edited in place."""
        return await gated_call(
            gate, READ, lambda: client.post("/rubrics", json_body={"name": name, "body": body})
        )

    @mcp.tool()
    async def dataworks_get_rubric(
        rubric_id: Annotated[str, Field(description="Rubric ID.", pattern=ID_PATTERN)],
    ) -> Any:
        """One rubric and its body."""
        return await gated_call(gate, READ, lambda: client.get(f"/rubrics/{rubric_id}"))

    @mcp.tool()
    async def dataworks_clone_rubric(
        rubric_id: Annotated[str, Field(description="Rubric to copy.", pattern=ID_PATTERN)],
        body: Annotated[
            dict[str, Any] | None, Field(description="A changed body; omit to copy as is.")
        ] = None,
    ) -> Any:
        """Copy a rubric as a new version, optionally with a changed body."""
        return await gated_call(
            gate,
            READ,
            lambda: client.post(f"/rubrics/{rubric_id}/clone", json_body={"body": body}),
        )

    # ------------------------------------------------------------ 005 endpoints and previews

    @mcp.tool()
    async def dataworks_test_endpoint_role(
        role: Annotated[
            Literal["classifier", "judge", "generation", "embeddings"],
            Field(description="The endpoint role to test."),
        ],
    ) -> Any:
        """Send one test request to the role's endpoint and report what it can do (logprobs,
        allowed tokens, structured output)."""
        return await gated_call(gate, READ, lambda: client.post(f"/endpoint-roles/{role}/test"))

    @mcp.tool()
    async def dataworks_label_sample(
        input_version_id: VersionId,
        role: LabelRole,
        template_id: TemplateId = None,
        rubric_id: RubricId = None,
        question: Question = None,
        field_map: FieldMap = None,
        threshold_positive: Threshold = None,
        threshold_negative: Threshold = None,
        min_top_probability: Threshold = None,
        rows: Annotated[int, Field(description="Rows to label, a small sample.", ge=1)] = 5,
        seed: Annotated[int, Field(description="Sampling seed.", ge=0)] = 0,
        row_filter: RowFilter = None,
    ) -> Any:
        """Label a few rows now and show each verdict, to check a template or rubric before a
        run. Writes no labels."""
        body = compact(
            {
                "input_version_id": input_version_id,
                "role": role,
                "template_id": template_id,
                "rubric_id": rubric_id,
                "question": question,
                "field_map": field_map,
                "threshold_positive": threshold_positive,
                "threshold_negative": threshold_negative,
                "min_top_probability": min_top_probability,
                "rows": rows,
                "seed": seed,
                "row_filter": row_filter,
            }
        )
        return await gated_call(
            gate, JOB_FILES, lambda: client.post("/labeling/sample", json_body=body)
        )

    @mcp.tool()
    async def dataworks_preview_band(
        input_version_id: VersionId,
        template_id: Annotated[str, Field(description="Decision template ID.", pattern=ID_PATTERN)],
        role: Annotated[
            Literal["classifier"], Field(description="Keep-share previews use the classifier.")
        ] = "classifier",
        question: Question = None,
        field_map: FieldMap = None,
        threshold_positive: Threshold = None,
        threshold_negative: Threshold = None,
        min_top_probability: Threshold = None,
        sample_rows: Annotated[int | None, Field(description="Rows to sample.", ge=1)] = None,
        seed: Annotated[int, Field(description="Sampling seed.", ge=0)] = 0,
        row_filter: RowFilter = None,
    ) -> Any:
        """Estimate the share of rows each threshold band would keep, on a sample. Starts a job;
        read it with dataworks_get_keep_share."""
        body = compact(
            {
                "input_version_id": input_version_id,
                "role": role,
                "template_id": template_id,
                "question": question,
                "field_map": field_map,
                "threshold_positive": threshold_positive,
                "threshold_negative": threshold_negative,
                "min_top_probability": min_top_probability,
                "sample_rows": sample_rows,
                "seed": seed,
                "row_filter": row_filter,
            }
        )
        return await gated_call(
            gate, JOB_FILES, lambda: client.post("/labeling/keep-share", json_body=body)
        )

    @mcp.tool()
    async def dataworks_get_keep_share(
        job_id: Annotated[str, Field(description="Keep-share job ID.", pattern=ID_PATTERN)],
    ) -> Any:
        """A keep-share preview's result: kept share per band, on its sample."""
        return await gated_call(gate, READ, lambda: client.get(f"/labeling/keep-share/{job_id}"))

    # ------------------------------------------------------------ 005 label runs

    @mcp.tool()
    async def dataworks_plan_label_run(
        input_version_id: VersionId,
        role: LabelRole,
        template_id: TemplateId = None,
        rubric_id: RubricId = None,
        question: Question = None,
        field_map: FieldMap = None,
        threshold_positive: Threshold = None,
        threshold_negative: Threshold = None,
        min_top_probability: Threshold = None,
        positive_label: Label = None,
        negative_label: Label = None,
        sampling: SamplingArg = None,
        chunk_size: Chunk = None,
        row_filter: RowFilter = None,
        keep_share_job_id: KeepShare = None,
        transport: Transport = "single",
        probe_id: ProbeId = None,
        window: ProbeWindow = None,
        sae_read: SaeRead = None,
        reproduction_retry_reason: RetryReason = None,
    ) -> Any:
        """What a label run would do before starting it: rows to score, rows reused from cache,
        the agent rows already in the 24-hour window, and whether it would wait for approval. A
        probe-verdict run (role 'probe') also shows the probe and its reproduction check."""
        body = _start_body(
            input_version_id, role, template_id, rubric_id, question, field_map,
            threshold_positive, threshold_negative, min_top_probability, positive_label,
            negative_label, sampling, chunk_size, row_filter, keep_share_job_id, transport,
            probe_id, window, sae_read, reproduction_retry_reason,
        )  # fmt: skip
        request = json.dumps(body, sort_keys=True)
        return await gated_call(gate, READ, lambda: client.get("/label-runs/plan", request=request))

    @mcp.tool()
    async def dataworks_start_label_run(
        input_version_id: VersionId,
        role: LabelRole,
        template_id: TemplateId = None,
        rubric_id: RubricId = None,
        question: Question = None,
        field_map: FieldMap = None,
        threshold_positive: Threshold = None,
        threshold_negative: Threshold = None,
        min_top_probability: Threshold = None,
        positive_label: Label = None,
        negative_label: Label = None,
        sampling: SamplingArg = None,
        chunk_size: Chunk = None,
        row_filter: RowFilter = None,
        keep_share_job_id: KeepShare = None,
        transport: Transport = "single",
        probe_id: ProbeId = None,
        window: ProbeWindow = None,
        sae_read: SaeRead = None,
        reproduction_retry_reason: RetryReason = None,
    ) -> Any:
        """Start a label run on a version. Starts a job. When the run would take this version's
        agent total over 5,000 rows in 24 hours it waits for the operator's approval
        (agent_label_rows) and returns {"approval_id", "status": "pending", ...}; check first
        with dataworks_plan_label_run. Role 'probe' with probe_id starts a probe-verdict run:
        miLLM scores each row with that probe, after a reproduction check against miStudio."""
        body = _start_body(
            input_version_id, role, template_id, rubric_id, question, field_map,
            threshold_positive, threshold_negative, min_top_probability, positive_label,
            negative_label, sampling, chunk_size, row_filter, keep_share_job_id, transport,
            probe_id, window, sae_read, reproduction_retry_reason,
        )  # fmt: skip
        return await gated_call(gate, JOB_FILES, lambda: client.post("/label-runs", json_body=body))

    @mcp.tool()
    async def dataworks_list_millm_probes() -> Any:
        """The probes imported in miLLM that a probe-verdict run can score with: what each was
        fitted on, its bar and evidence rung, and whether it fits miLLM's loaded model."""
        return await gated_call(gate, READ, lambda: client.get("/labeling/probes"))

    @mcp.tool()
    async def dataworks_list_label_runs(
        input_version_id: Annotated[
            str | None, Field(description="Filter by input version.", pattern=ID_PATTERN)
        ] = None,
        state: Annotated[str | None, Field(description="Filter by state.", max_length=24)] = None,
        kind: Annotated[str | None, Field(description="Filter by kind.", max_length=16)] = None,
        page: Page = 1,
        limit: Limit = 50,
    ) -> Any:
        """List label runs with state, counts and who started them."""
        return await gated_call(
            gate,
            READ,
            lambda: client.get(
                "/label-runs",
                input_version_id=input_version_id,
                state=state,
                kind=kind,
                page=page,
                limit=limit,
            ),
        )

    @mcp.tool()
    async def dataworks_get_label_run(run_id: RunId) -> Any:
        """One label run: configuration, progress, counts per outcome, error."""
        return await gated_call(gate, READ, lambda: client.get(f"/label-runs/{run_id}"))

    @mcp.tool()
    async def dataworks_get_label_run_labels(
        run_id: RunId,
        outcome: Annotated[
            str | None, Field(description="Filter by outcome.", max_length=128)
        ] = None,
        page: Page = 1,
        limit: Annotated[
            int, Field(description="Labels per page, at most 500.", ge=1, le=500)
        ] = 100,
    ) -> Any:
        """A page of a run's labels: row key, verdict, probability and outcome."""
        return await gated_call(
            gate,
            FILES,
            lambda: client.get(
                f"/label-runs/{run_id}/labels", outcome=outcome, page=page, limit=limit
            ),
        )

    @mcp.tool()
    async def dataworks_resume_label_run(run_id: RunId) -> Any:
        """Resume a stopped or failed label run from its last chunk. An unchanged resume of an
        approved run needs no new approval."""
        return await gated_call(
            gate, JOB_FILES, lambda: client.post(f"/label-runs/{run_id}/resume")
        )

    @mcp.tool()
    async def dataworks_rederive_label_run(
        run_id: RunId,
        threshold_positive: Threshold = None,
        threshold_negative: Threshold = None,
        min_top_probability: Threshold = None,
    ) -> Any:
        """Re-derive a classifier run's labels from its stored probabilities with new thresholds.
        Calls no endpoint."""
        body = compact(
            {
                "threshold_positive": threshold_positive,
                "threshold_negative": threshold_negative,
                "min_top_probability": min_top_probability,
            }
        )
        return await gated_call(
            gate, FILES, lambda: client.post(f"/label-runs/{run_id}/rederive", json_body=body)
        )

    @mcp.tool()
    async def dataworks_aggregate_judges(
        run_ids: Annotated[
            list[str],
            Field(description="Label runs to combine, 2 to 16.", min_length=2, max_length=16),
        ],
    ) -> Any:
        """Combine several judge runs on one version into one verdict per row."""
        return await gated_call(
            gate,
            FILES,
            lambda: client.post("/label-runs/aggregate", json_body={"run_ids": run_ids}),
        )
