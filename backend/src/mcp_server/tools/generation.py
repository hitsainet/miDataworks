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
from ..context import (
    FILES,
    ID_PATTERN,
    JOB,
    JOB_FILES,
    READ,
    ArchiveReason,
    BehaviourFilter,
    BehaviourKeyArg,
    IncludeArchived,
    Limit,
    Page,
    Substitutions,
    ToolContext,
    archive_body,
)
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
    Literal["sft", "kto", "grpo_prompt", "dpo", "detector"],
    Field(description="The rows' target type: a TRL type, or detector (a carrier run's; 017)."),
]


def compact(body: dict[str, Any]) -> dict[str, Any]:
    """The body without unset fields, so the backend's own defaults apply."""
    return {k: v for k, v in body.items() if v is not None}


#: 017 carrier modes' arguments (FTID 017 §5.2): the plan's own generator, templates and top-up.
GeneratorModel = Annotated[
    str | None,
    Field(
        description="Carrier modes: the model that writes the carriers, sent as the request's "
        "model (one run per generator; the endpoint must serve it).",
        min_length=1,
        max_length=200,
    ),
]
TemplateMapArg = Annotated[
    dict[str, Any] | None,
    Field(
        description='Carrier modes: {"column": <plan column, e.g. genre>, "templates": {<value>: '
        "<respond template id>}}; every value the plan holds must be mapped."
    ),
]
RuleSetsArg = Annotated[
    list[dict[str, Any]] | None,
    Field(
        description="Carrier modes: rule-set pins [{name, version, sha256}] the template check "
        "runs over every mapped template (slot_template_has_element on a hit).",
        max_length=16,
    ),
]
RespondsToArg = Annotated[
    dict[str, Any] | None,
    Field(
        description='Carrier top-up: {"version_id": <earlier version>, "cells": [{genre, '
        "placement, framing, length_band}]}; only those plan cells are generated."
    ),
]
CarrierSampleSize = Annotated[
    int | None,
    Field(
        description="Seed rows to draw. Carrier modes take every plan row: leave it out.",
        ge=1,
        le=100_000,
    ),
]


CheckPhrasesArg = Annotated[
    list[str] | None,
    Field(
        description="Phrases the template must not hold (the behaviour's declared phrase list).",
        max_length=256,
    ),
]


def _carrier_fields(
    generator_model_id: str | None,
    template_map: dict[str, Any] | None,
    rule_sets: list[dict[str, Any]] | None,
    responds_to: dict[str, Any] | None,
    behaviour_key: str | None,
) -> dict[str, Any]:
    return {
        "generator_model_id": generator_model_id,
        "template_map": template_map,
        "rule_sets": rule_sets,
        "responds_to": responds_to,
        "behaviour_key": behaviour_key,
    }


def _run_body(
    mode: str,
    input_version_id: str,
    prompt_column: str,
    seed_splits: list[str],
    sample_size: int | None,
    target_type: str,
    seed: int | None,
    n_responses: int | None,
    expand_template_id: str | None,
    respond_template_id: str | None,
    generator_setting: dict[str, Any] | None = None,
    setting_a: dict[str, Any] | None = None,
    setting_b: dict[str, Any] | None = None,
    chosen_side: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A run body without unset fields; ``extra`` carries 017's fields (``behaviour_key``)."""
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
            **(extra or {}),
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
        behaviour_key: BehaviourFilter = None,
    ) -> Any:
        """List generation templates (expand: seed row → new prompt; respond: prompt → answer)."""
        return await gated_call(
            gate,
            READ,
            lambda: client.get(
                "/generation-templates",
                kind=kind,
                page=page,
                limit=limit,
                behaviour_key=behaviour_key,
            ),
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
        behaviour_key: BehaviourKeyArg = None,
        check_rule_sets: RuleSetsArg = None,
        check_phrases: CheckPhrasesArg = None,
    ) -> Any:
        """Create a generation template (version 1). Edit by cloning; a used one never changes.
        behaviour_key is recorded on the template, not in its hash. With check_rule_sets or
        check_phrases the template is refused (slot_template_has_element) when a signal fires on
        its text or it holds a phrase: a carrier template must never show the element."""
        return await gated_call(
            gate,
            READ,
            lambda: client.post(
                "/generation-templates",
                json_body=compact(
                    {
                        "name": name,
                        "kind": kind,
                        "body": body,
                        "description": description,
                        "behaviour_key": behaviour_key,
                        "check_rule_sets": check_rule_sets,
                        "check_phrases": check_phrases,
                    }
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
        behaviour_key: BehaviourKeyArg = None,
        check_rule_sets: RuleSetsArg = None,
        check_phrases: CheckPhrasesArg = None,
        substitutions: Substitutions = None,
    ) -> Any:
        """Copy a generation template as the next version, optionally with a changed body or
        substitutions into it (JSON Pointers, in order). The copy keeps the source's
        behaviour_key unless one is given. check_rule_sets and check_phrases check the copy's
        text as dataworks_save_generation_template does. Read what changed with
        dataworks_get_clone_difference."""
        return await gated_call(
            gate,
            READ,
            lambda: client.post(
                f"/generation-templates/{template_id}/clone",
                json_body=compact(
                    {
                        "body": body,
                        "behaviour_key": behaviour_key,
                        "check_rule_sets": check_rule_sets,
                        "check_phrases": check_phrases,
                        "substitutions": substitutions or None,
                    }
                ),
            ),
        )

    # ------------------------------------------------------------------- runs

    @mcp.tool()
    async def dataworks_plan_generation(
        input_version_id: VersionId,
        prompt_column: Annotated[
            str,
            Field(
                description="Column holding each seed's prompt (carrier modes: the plan's text "
                "column the carriers are written into)."
            ),
        ],
        seed_splits: Splits,
        target_type: Target,
        sample_size: CarrierSampleSize = None,
        mode: Annotated[
            Literal["standard", "steered_pairs", "carrier_slot", "carrier_instructed"],
            Field(
                description="Run mode. carrier_slot: one carrier per plan row, written around "
                "the run's slot token; carrier_instructed: one whole text per plan row from its "
                "planned cells (017)."
            ),
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
        behaviour_key: BehaviourKeyArg = None,
        generator_model_id: GeneratorModel = None,
        template_map: TemplateMapArg = None,
        rule_sets: RuleSetsArg = None,
        responds_to: RespondsToArg = None,
    ) -> Any:
        """Dry-run a generation run: held-out status, counts, identities and every refusal.
        Writes nothing. A carrier plan also states the templates per plan value, the plan rows
        taken and the estimated run hours ("not measured" before a measured run)."""
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
            _carrier_fields(
                generator_model_id, template_map, rule_sets, responds_to, behaviour_key
            ),
        )
        return await gated_call(
            gate, FILES, lambda: client.post("/generation-runs/plan", json_body=body)
        )

    @mcp.tool()
    async def dataworks_start_generation(
        input_version_id: VersionId,
        prompt_column: Annotated[
            str,
            Field(
                description="Column holding each seed's prompt (carrier modes: the plan's text "
                "column the carriers are written into)."
            ),
        ],
        seed_splits: Splits,
        target_type: Target,
        sample_size: CarrierSampleSize = None,
        mode: Annotated[
            Literal["standard", "carrier_slot", "carrier_instructed"],
            Field(
                description="standard; or a carrier mode (017): one carrier per plan row of a "
                "carrier_plan@1 version, by generator_model_id, templates from template_map."
            ),
        ] = "standard",
        respond_template_id: OptionalTemplate = None,
        expand_template_id: OptionalTemplate = None,
        n_responses: Annotated[
            int | None, Field(description="Responses per prompt.", ge=1, le=16)
        ] = None,
        seed: Annotated[int | None, Field(description="Sampling seed.", ge=0)] = None,
        generator_setting: Annotated[
            dict[str, Any] | None, Field(description="Steering of the generator (default none).")
        ] = None,
        behaviour_key: BehaviourKeyArg = None,
        generator_model_id: GeneratorModel = None,
        template_map: TemplateMapArg = None,
        rule_sets: RuleSetsArg = None,
        responds_to: RespondsToArg = None,
    ) -> Any:
        """Start a generation run from a version with a held-out split: standard, or a carrier
        mode over a carrier plan (017; slot carriers holding the slot token zero or twice are
        dropped and counted). The judge must be a different model (JUDGE_IS_GENERATOR
        otherwise). behaviour_key is recorded on the run at start and stands over keys found on
        related records."""
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
            extra=_carrier_fields(
                generator_model_id, template_map, rule_sets, responds_to, behaviour_key
            ),
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
            Literal[
                "standard", "steered_pairs", "minimal_pairs", "carrier_slot", "carrier_instructed"
            ]
            | None,
            Field(description="Only this mode."),
        ] = None,
        include_archived: IncludeArchived = False,
        page: Page = 1,
        limit: Limit = 50,
        behaviour_key: BehaviourFilter = None,
    ) -> Any:
        """List generation runs, newest first. Archived runs are left out unless
        include_archived is true."""
        return await gated_call(
            gate,
            READ,
            lambda: client.get(
                "/generation-runs",
                input_version_id=input_version_id,
                state=state,
                mode=mode,
                include_archived=include_archived,
                page=page,
                limit=limit,
                behaviour_key=behaviour_key,
            ),
        )

    @mcp.tool()
    async def dataworks_archive_generation_run(run_id: RunId, reason: ArchiveReason = None) -> Any:
        """Archive a finished generation run: hide it from default lists and pickers. Its
        records and pairs still read; dataworks_unarchive_generation_run restores it. Refused
        (run_not_finished) while it is queued or running."""
        return await gated_call(
            gate,
            READ,
            lambda: client.post(
                f"/generation-runs/{run_id}/archive", json_body=archive_body(reason)
            ),
        )

    @mcp.tool()
    async def dataworks_unarchive_generation_run(
        run_id: RunId, reason: ArchiveReason = None
    ) -> Any:
        """Unarchive a generation run: list it again."""
        return await gated_call(
            gate,
            READ,
            lambda: client.post(
                f"/generation-runs/{run_id}/unarchive", json_body=archive_body(reason)
            ),
        )

    @mcp.tool()
    async def dataworks_get_generation_run(run_id: RunId) -> Any:
        """One generation run: state, counts by outcome, snapshots, identities, pinning, timing
        (queued, started, finished, durations), the behaviour it is for, its dataset and templates
        by name, and the candidate versions built from it."""
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
        role: Annotated[
            Literal["judge", "classifier"],
            Field(
                description="judge (default) or classifier: the second-opinion classifier "
                "against the generators of generator_run_ids (017)."
            ),
        ] = "judge",
        generator_run_ids: Annotated[
            list[str] | None,
            Field(
                description="role classifier: the generation runs whose recorded generators "
                "are compared.",
                min_length=1,
                max_length=64,
            ),
        ] = None,
    ) -> Any:
        """Compare the judge's identity with the generator's (model, revision, steering hash).
        With role classifier, compare the classifier endpoint with the generators the named runs
        recorded; an endpoint that does not disclose its model reads "not reported", never
        independent."""
        body = compact(
            {
                "mode": mode,
                "generator_setting": generator_setting,
                "setting_a": setting_a,
                "setting_b": setting_b,
                "role": role,
                "generator_run_ids": generator_run_ids,
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
        """The version's newest diversity report: figures, controls, verdict, method_version."""
        return await gated_call(
            gate, READ, lambda: client.get(f"/versions/{version_id}/diversity", column=column)
        )

    @mcp.tool()
    async def dataworks_run_diversity_report(
        version_id: VersionId,
        column: Annotated[str | None, Field(description="Column to measure.")] = None,
        breakdown_by: Annotated[
            list[str] | None,
            Field(
                description="017: break the report down by these columns (generator, genre): "
                "each value against the others; a value whose whole interval lies below the "
                "others' reads falls.",
                min_length=1,
                max_length=4,
            ),
        ] = None,
    ) -> Any:
        """Start a diversity report against the version's reference (a falling verdict warns and
        refuses nothing). An all-carrier version has no reference ("reference: not measured");
        its breakdown still compares generators and genres with each other."""
        return await gated_call(
            gate,
            JOB_FILES,
            lambda: client.post(
                f"/versions/{version_id}/diversity",
                json_body=compact({"column": column, "breakdown_by": breakdown_by}),
            ),
        )
