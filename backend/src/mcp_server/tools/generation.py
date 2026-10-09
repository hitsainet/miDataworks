"""Category ``generation``: feature 007's synthetic generation and steered pairs (FR-007.45).

Eighteen tools over the routes of 007 FTDD section 5.1 (010 FTID section 3.7, plus the five
FTDD 007 change (f) names). HTTP only (``test_mcp_tools_stay_on_http.py``). No tool takes a secret
and none is gated: an agent's start runs at once and returns its job (FR-007.30, FR-007.31); a
judge run over generated rows goes through 005's gated label-run tool. Run cancel has no tool of
its own: ``dataworks_cancel_job`` reaches the same cancellation.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from ..client import DataworksClient
from ..context import FILES, ID_PATTERN, JOB, JOB_FILES, READ, Limit, Page, ToolContext
from ..health_gate import gated_call

VersionId = Annotated[str, Field(description="Version ID.", pattern=ID_PATTERN)]
RunId = Annotated[str, Field(description="Generation run ID, gr_...", pattern=ID_PATTERN)]
TemplateId = Annotated[str, Field(description="Generation template ID, gt_...", pattern=ID_PATTERN)]
OptionalTemplate = Annotated[
    str | None, Field(description="Generation template ID, gt_...", pattern=ID_PATTERN)
]
Setting = Annotated[
    dict[str, Any],
    Field(
        description='A steering setting: {"kind": "none"}, {"kind": "profile", "profile_name": '
        '"humor"}, or {"kind": "inline", "sae_id": "...", "features": [{"index": 4127, '
        '"strength": 6.0}]}.'
    ),
]
Splits = Annotated[list[str], Field(description="Splits to draw seed rows from (never held out).")]
Target = Annotated[
    Literal["sft", "kto", "grpo_prompt", "dpo"], Field(description="The rows' TRL target type.")
]


def compact(body: dict[str, Any]) -> dict[str, Any]:
    """The body without unset fields, so the backend's own defaults apply."""
    return {k: v for k, v in body.items() if v is not None}


def _run_body(
    mode: str,
    input_version_id: str,
    prompt_column: str,
    seed_splits: list[str],
    sample_size: int,
    target_type: str,
    seed: int | None,
    n_responses: int | None,
    expand_template_id: str | None,
    respond_template_id: str | None,
    generator_setting: dict[str, Any] | None = None,
    setting_a: dict[str, Any] | None = None,
    setting_b: dict[str, Any] | None = None,
    chosen_side: str | None = None,
) -> dict[str, Any]:
    return compact(
        {
            "mode": mode,
            "input_version_id": input_version_id,
            "prompt_column": prompt_column,
            "seed_splits": seed_splits,
            "sample_size": sample_size,
            "seed": seed,
            "n_responses": n_responses,
            "expand_template_id": expand_template_id,
            "respond_template_id": respond_template_id,
            "generator_setting": generator_setting,
            "setting_a": setting_a,
            "setting_b": setting_b,
            "chosen_side": chosen_side,
            "target_type": target_type,
        }
    )


def register(mcp: MCPServer, client: DataworksClient, ctx: ToolContext) -> None:
    gate = ctx.gate

    # ------------------------------------------------------------------- templates

    @mcp.tool()
    async def dataworks_list_generation_templates(
        kind: Annotated[
            Literal["expand", "respond"] | None, Field(description="Only this kind.")
        ] = None,
        page: Page = 1,
        limit: Limit = 50,
    ) -> Any:
        """List generation templates (expand: seed row → new prompt; respond: prompt → answer)."""
        return await gated_call(
            gate,
            READ,
            lambda: client.get("/generation-templates", kind=kind, page=page, limit=limit),
        )

    @mcp.tool()
    async def dataworks_save_generation_template(
        name: Annotated[str, Field(description="Template name (lowercase).", min_length=1)],
        kind: Annotated[Literal["expand", "respond"], Field(description="Template kind.")],
        body: Annotated[
            dict[str, Any],
            Field(description="{prompt with {column} placeholders, system?, sampling?}."),
        ],
        description: Annotated[str | None, Field(description="What it is for.")] = None,
    ) -> Any:
        """Create a generation template (version 1). Edit by cloning; a used one never changes."""
        return await gated_call(
            gate,
            READ,
            lambda: client.post(
                "/generation-templates",
                json_body=compact(
                    {"name": name, "kind": kind, "body": body, "description": description}
                ),
            ),
        )

    @mcp.tool()
    async def dataworks_get_generation_template(template_id: TemplateId) -> Any:
        """One generation template, its body, hash and placeholders."""
        return await gated_call(
            gate, READ, lambda: client.get(f"/generation-templates/{template_id}")
        )

    @mcp.tool()
    async def dataworks_clone_generation_template(
        template_id: TemplateId,
        body: Annotated[
            dict[str, Any] | None, Field(description="A changed body; omit to copy as is.")
        ] = None,
    ) -> Any:
        """Copy a generation template as the next version, optionally with a changed body."""
        return await gated_call(
            gate,
            READ,
            lambda: client.post(
                f"/generation-templates/{template_id}/clone", json_body=compact({"body": body})
            ),
        )

    # ------------------------------------------------------------------- runs

    @mcp.tool()
    async def dataworks_plan_generation(
        input_version_id: VersionId,
        prompt_column: Annotated[str, Field(description="Column holding each seed's prompt.")],
        seed_splits: Splits,
        sample_size: Annotated[int, Field(description="Seed rows to draw.", ge=1, le=100_000)],
        target_type: Target,
        mode: Annotated[
            Literal["standard", "steered_pairs"], Field(description="Run mode.")
        ] = "standard",
        seed: Annotated[int | None, Field(description="Sampling seed.", ge=0)] = None,
        n_responses: Annotated[
            int | None, Field(description="Responses per prompt.", ge=1, le=16)
        ] = None,
        expand_template_id: OptionalTemplate = None,
        respond_template_id: OptionalTemplate = None,
        generator_setting: Annotated[
            dict[str, Any] | None, Field(description="Standard mode steering.")
        ] = None,
        setting_a: Annotated[dict[str, Any] | None, Field(description="Steered side A.")] = None,
        setting_b: Annotated[dict[str, Any] | None, Field(description="Steered side B.")] = None,
        chosen_side: Annotated[
            Literal["a", "b"] | None, Field(description="Preferred side.")
        ] = None,
    ) -> Any:
        """Dry-run a generation run: held-out status, counts, identities and every refusal.
        Writes nothing."""
        body = _run_body(
            mode,
            input_version_id,
            prompt_column,
            seed_splits,
            sample_size,
            target_type,
            seed,
            n_responses,
            expand_template_id,
            respond_template_id,
            generator_setting,
            setting_a,
            setting_b,
            chosen_side,
        )
        return await gated_call(
            gate, FILES, lambda: client.post("/generation-runs/plan", json_body=body)
        )

    @mcp.tool()
    async def dataworks_start_generation(
        input_version_id: VersionId,
        prompt_column: Annotated[str, Field(description="Column holding each seed's prompt.")],
        seed_splits: Splits,
        sample_size: Annotated[int, Field(description="Seed rows to draw.", ge=1, le=100_000)],
        target_type: Target,
        respond_template_id: OptionalTemplate = None,
        expand_template_id: OptionalTemplate = None,
        n_responses: Annotated[
            int | None, Field(description="Responses per prompt.", ge=1, le=16)
        ] = None,
        seed: Annotated[int | None, Field(description="Sampling seed.", ge=0)] = None,
        generator_setting: Annotated[
            dict[str, Any] | None, Field(description="Steering of the generator (default none).")
        ] = None,
    ) -> Any:
        """Start a standard generation run from a version with a held-out split. The judge must
        be a different model (JUDGE_IS_GENERATOR otherwise)."""
        body = _run_body(
            "standard",
            input_version_id,
            prompt_column,
            seed_splits,
            sample_size,
            target_type,
            seed,
            n_responses,
            expand_template_id,
            respond_template_id,
            generator_setting,
        )
        return await gated_call(
            gate, JOB_FILES, lambda: client.post("/generation-runs", json_body=body)
        )

    @mcp.tool()
    async def dataworks_start_steered_pairs(
        input_version_id: VersionId,
        prompt_column: Annotated[str, Field(description="Column holding each seed's prompt.")],
        seed_splits: Splits,
        sample_size: Annotated[int, Field(description="Seed rows to draw.", ge=1, le=100_000)],
        respond_template_id: TemplateId,
        setting_a: Setting,
        setting_b: Setting,
        chosen_side: Annotated[Literal["a", "b"], Field(description="The side DPO prefers.")],
        seed: Annotated[int | None, Field(description="Sampling seed.", ge=0)] = None,
    ) -> Any:
        """Start a steered-pair run: two settings differing on exactly ONE SAE feature, the same
        prompt and seed per pair; a pair is kept only when miLLM reports both settings applied."""
        body = _run_body(
            "steered_pairs",
            input_version_id,
            prompt_column,
            seed_splits,
            sample_size,
            "dpo",
            seed,
            None,
            None,
            respond_template_id,
            None,
            setting_a,
            setting_b,
            chosen_side,
        )
        return await gated_call(
            gate, JOB_FILES, lambda: client.post("/generation-runs", json_body=body)
        )

    @mcp.tool()
    async def dataworks_list_generation_runs(
        input_version_id: Annotated[
            str | None, Field(description="Only runs from this version.", pattern=ID_PATTERN)
        ] = None,
        state: Annotated[str | None, Field(description="Run state.", max_length=24)] = None,
        mode: Annotated[
            Literal["standard", "steered_pairs"] | None, Field(description="Only this mode.")
        ] = None,
        page: Page = 1,
        limit: Limit = 50,
    ) -> Any:
        """List generation runs, newest first."""
        return await gated_call(
            gate,
            READ,
            lambda: client.get(
                "/generation-runs",
                input_version_id=input_version_id,
                state=state,
                mode=mode,
                page=page,
                limit=limit,
            ),
        )

    @mcp.tool()
    async def dataworks_get_generation_run(run_id: RunId) -> Any:
        """One generation run: state, counts by outcome, snapshots, identities, pinning."""
        return await gated_call(gate, READ, lambda: client.get(f"/generation-runs/{run_id}"))

    @mcp.tool()
    async def dataworks_get_generation_records(
        run_id: RunId,
        outcome: Annotated[
            Literal["generated", "discarded", "skipped"] | None, Field(description="Filter.")
        ] = None,
        stage: Annotated[Literal["expand", "respond"] | None, Field(description="Filter.")] = None,
        page: Page = 1,
        limit: Limit = 50,
    ) -> Any:
        """A page of generation records: requested and reported steering, check, seed, reason."""
        return await gated_call(
            gate,
            FILES,
            lambda: client.get(
                f"/generation-runs/{run_id}/records",
                outcome=outcome,
                stage=stage,
                page=page,
                limit=limit,
            ),
        )

    @mcp.tool()
    async def dataworks_get_generation_pairs(
        run_id: RunId, page: Page = 1, limit: Limit = 50
    ) -> Any:
        """A page of the steered pairs a run kept (both sides matched)."""
        return await gated_call(
            gate,
            FILES,
            lambda: client.get(f"/generation-runs/{run_id}/pairs", page=page, limit=limit),
        )

    @mcp.tool()
    async def dataworks_resume_generation(run_id: RunId) -> Any:
        """Resume a cancelled or failed run with a new job; committed chunks are kept. A run that
        failed because a profile changed cannot be resumed."""
        return await gated_call(gate, JOB, lambda: client.post(f"/generation-runs/{run_id}/resume"))

    @mcp.tool()
    async def dataworks_preview_generation(
        prompts: Annotated[
            list[str] | None,
            Field(
                description="Up to 5 bare prompts (only for a template that reads {prompt} "
                "alone). Omit to preview from seed rows.",
                min_length=1,
                max_length=5,
            ),
        ] = None,
        input_version_id: Annotated[
            str | None,
            Field(description="Preview from REAL seed rows of this version (as a run seeds)."),
        ] = None,
        prompt_column: Annotated[
            str | None, Field(description="Seed-row mode: the column {prompt} renders from.")
        ] = None,
        seed_splits: Annotated[
            list[str] | None, Field(description="Seed-row mode: splits to draw seed rows from.")
        ] = None,
        sample_size: Annotated[
            int | None, Field(description="Seed-row mode: rows to draw (1-5).", ge=1, le=5)
        ] = None,
        respond_template_id: OptionalTemplate = None,
        setting: Annotated[dict[str, Any] | None, Field(description="Steering setting.")] = None,
        seed: Annotated[int | None, Field(description="Sampling and selection seed.", ge=0)] = None,
    ) -> Any:
        """Generate for up to 5 prompts or seed rows now (no lease, refuse-load) and show each
        response with miLLM's reported steering. A template that reads any column besides
        {prompt} needs seed rows (PREVIEW_NEEDS_ROWS otherwise). Writes nothing."""
        body = compact(
            {
                "prompts": prompts,
                "input_version_id": input_version_id,
                "prompt_column": prompt_column,
                "seed_splits": seed_splits,
                "sample_size": sample_size,
                "respond_template_id": respond_template_id,
                "setting": setting,
                "seed": seed,
            }
        )
        return await gated_call(
            gate, READ, lambda: client.post("/generation-runs/preview", json_body=body)
        )

    @mcp.tool()
    async def dataworks_compare_steering_settings(setting_a: Setting, setting_b: Setting) -> Any:
        """Resolve two steering settings and report which SAE feature indices differ."""
        return await gated_call(
            gate,
            READ,
            lambda: client.post(
                "/steering-settings/compare",
                json_body={"setting_a": setting_a, "setting_b": setting_b},
            ),
        )

    @mcp.tool()
    async def dataworks_check_judge_independence(
        mode: Annotated[
            Literal["standard", "steered_pairs"], Field(description="Run mode to check.")
        ] = "standard",
        generator_setting: Annotated[
            dict[str, Any] | None, Field(description="Standard mode steering.")
        ] = None,
        setting_a: Annotated[dict[str, Any] | None, Field(description="Steered side A.")] = None,
        setting_b: Annotated[dict[str, Any] | None, Field(description="Steered side B.")] = None,
    ) -> Any:
        """Compare the judge's identity with the generator's (model, revision, steering hash)."""
        body = compact(
            {
                "mode": mode,
                "generator_setting": generator_setting,
                "setting_a": setting_a,
                "setting_b": setting_b,
            }
        )
        return await gated_call(
            gate,
            READ,
            lambda: client.post("/generation-runs/independence-check", json_body=body),
        )

    @mcp.tool()
    async def dataworks_build_generation_candidate(
        run_id: RunId,
        seed: Annotated[int | None, Field(description="Version seed.", ge=0)] = None,
    ) -> Any:
        """Build candidate version C = the input version plus every generated row (no model is
        called; the build binds the run)."""
        return await gated_call(
            gate,
            JOB_FILES,
            lambda: client.post(
                f"/generation-runs/{run_id}/candidate-build", json_body=compact({"seed": seed})
            ),
        )

    # ------------------------------------------------------------------- diversity

    @mcp.tool()
    async def dataworks_get_diversity(
        version_id: VersionId,
        column: Annotated[str | None, Field(description="Measured column.")] = None,
    ) -> Any:
        """The version's newest diversity report: figures with intervals, controls, verdict."""
        return await gated_call(
            gate, READ, lambda: client.get(f"/versions/{version_id}/diversity", column=column)
        )

    @mcp.tool()
    async def dataworks_run_diversity_report(
        version_id: VersionId,
        column: Annotated[str | None, Field(description="Column to measure.")] = None,
    ) -> Any:
        """Start a diversity report against the version's reference (a falling verdict warns and
        refuses nothing)."""
        return await gated_call(
            gate,
            JOB_FILES,
            lambda: client.post(
                f"/versions/{version_id}/diversity", json_body=compact({"column": column})
            ),
        )
