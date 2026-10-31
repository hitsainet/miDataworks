"""Category ``core``: health, guidance, jobs and approvals (FR-010.19, .20, .21, .24).

Foundation routes: ``GET /api/health`` (3.7), the jobs routes (5.7), the approval routes (9.3).
``dataworks_howto`` makes no request; it is the server's own guidance.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from ..client import DataworksClient
from ..context import (
    ID_PATTERN,
    READ,
    ArchiveReason,
    IncludeArchived,
    Limit,
    Page,
    ToolContext,
    archive_body,
)
from ..health_gate import gated_call

# The approval actions an agent's request can wait on, in the howto's words. The MCP server has no
# import of the backend's core (ADR-016: its own deployment), so this list is kept here and
# tests/unit/mcp/test_core_tools.py pins its names equal to core.agent_origin.APPROVAL_ACTIONS; the
# count in the text is derived from it (ENH-008 section 3.3 item 6: it once said "Nine" over ten).
HOWTO_APPROVAL_ACTIONS: tuple[tuple[str, str], ...] = (
    ("hub_push", "any push to the Hub"),
    ("agent_label_rows", "over 5,000 agent-labelled rows per version per 24 hours"),
    ("version_delete", "deleting a version"),
    (
        "millm_model_load",
        "miDataworks's own gate for loading a model in miLLM, not miLLM's; no route uses it yet",
    ),
    ("secret_write", "an HF token or an API key"),
    ("source_annotate", "a source's licence, terms or detection override"),
    ("gate_target_write", "a calibration gate target or a probe reproduction link"),
    ("config_write", "changing or resetting a runtime environment setting"),
    (
        "paid_classifier_run",
        "any label run, start or resume, on a paid classifier endpoint, whatever its size",
    ),
    ("code_rule_save", "saving a code rule whose content hash is not approved"),
)

_NUMBER_WORDS = (
    "Zero One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve Thirteen Fourteen "
    "Fifteen Sixteen Seventeen Eighteen Nineteen Twenty"
).split()


def _count_word(n: int) -> str:
    return _NUMBER_WORDS[n] if n < len(_NUMBER_WORDS) else str(n)


def _approvals_howto() -> str:
    listed = "; ".join(f"{name} ({what})" for name, what in HOWTO_APPROVAL_ACTIONS)
    return (
        f"{_count_word(len(HOWTO_APPROVAL_ACTIONS))} actions wait for the operator when an agent "
        f"asks: {listed}. The operator decides each one on miDataworks's Approvals screen; "
        "dataworks_list_approvals and dataworks_get_approval_status read them. The approval "
        "binds to the exact request: changing any field after approval needs a new approval."
    )


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
    "approvals": _approvals_howto(),
    "jobs": (
        "Imports, builds, publish builds, checks, publishes and exports run as jobs. A route that "
        "starts one returns a job ID; poll dataworks_job_status. A build's message names its "
        "current step and whether it is queued for a worker, running, recording output or waiting "
        "on another build computing the same step. dataworks_cancel_job requests a cooperative "
        "stop: the job stops at its next checkpoint (a build that is only waiting stops at once). "
        "Heavy reads (dataworks_list_jobs, dataworks_check_detector_set, the shortcut audit) "
        "answer a summary by default; pass detail=full for everything."
    ),
    "data_designer": (
        "Data Designer (dd_*) steps. A template sees the version's content columns and any other "
        "non-system column it names: a metadata column, or an earlier dd_* step's output (always "
        "metadata), so chained dd_* steps work in ONE build. _dw_* columns are never visible. "
        "Templates follow Data Designer's sandbox: no {% set %}, include, import, macro, extends "
        "or block; no loop inside a loop; no {{ self }}; at most 600 nodes and nesting depth 10; "
        "only Data Designer's filters (abs, capitalize, first, float, int, items, last, length, "
        "list, lower, max, min, replace, reverse, round, sort, string, title, trim, truncate, "
        "unique, upper, urlencode, jsonpath, ...). Check before building: "
        "dataworks_validate_operator_params (pass columns to check references) or "
        "dataworks_validate_recipe (pass columns, or use dataworks_plan_build, to check each "
        "template against the columns its step will really see); both name the rule that fails "
        "(too_deep, too_many_nodes, unknown_reference ...). A recipe holds at most 64 steps. "
        'Types are kept: a column stored as text reaches the template as text ("9" is never 9, '
        "JSON text never an object), and an expression's output is stored as its dtype. A "
        "preview shows each example's computed values. A failed preview reads status "
        "failed with Data Designer's message. A row an expression renders empty for is dropped "
        "with reason dd.empty_render (an intended skip); dd.render_error, dd.type_cast_error and "
        "dd.not_generated are failures. dd_expression is deterministic; dd_llm_* are not."
    ),
    "identifiers": (
        "Identifier and pattern data (identifier dictionaries) needs no template chain. Rows from "
        "nothing: dataworks_create_index_source(rows) gives one integer column (build with "
        "column_roles {idx: content}). synth_identifier makes one value per row for a scheme "
        "(card_pan, ssn_us, aba_routing, iban, nhs, aadhaar) from a seed: valid_share valid "
        "values in your delimiter styles, the rest with a named perturbation (bad_check_digit, "
        "wrong_length, mixed_delimiters, adjacent_letters, bad_prefix / bad_range for SSN, "
        "masked), writing value, scheme, perturbation, valid and detail. checksum_validate checks "
        "a column with the same rules and writes cv_valid, cv_reason, cv_tier (none/low/medium "
        "per the documented confidence tables), cv_layout and the value's span cv_start/cv_end "
        "(mode=value with before_column/after_column for the neighbouring text; mode=scan for "
        "free text). phrase_proximity measures the nearest listed phrase to that span in UTF-8 "
        "bytes (pp_distance, pp_phrase, pp_near within max_distance_bytes; key_phrases adds "
        "per-row phrases such as an IBAN's country name). regex_match matches an RE2-like "
        'pattern and records spans. rule_label writes a column from ordered rules ("high if '
        'cv_tier == medium and pp_near == true", default "@cv_tier"). drop_columns removes '
        "helper columns. A Credit Cards labelling stage is 4 steps: checksum_validate, "
        "phrase_proximity, rule_label (tier), rule_label (label). Every one is deterministic and "
        "calls no model; previews show the values computed."
    ),
    "rule_signals": (
        "Rule signals (feature 016) decide facts about text deterministically, the same way in "
        "every app (the contract docs/schemas/rule-signal-cases-v1.json). Validators: "
        "dataworks_list_validators and dataworks_get_validator list the formats (card_pan, "
        "ssn_us, aba_routing, iban, nhs, aadhaar, masked) and algorithms (luhn, mod97_10, "
        "verhoeff, weighted_mod, length_charset), each by exact version, with its reasons and "
        "cases. A rule set (format midataworks.rule-set/v1, docs/schemas/midataworks-rule-set-v1"
        ".json) has signals (pattern in an RE2 dialect, proximity, validator, lexical_weights), "
        "named expressions (signal, all, any, not, within_n), outputs (fired, count, first, "
        "spans, field), vetoes and the cases it must pass. Spans are in characters (code "
        "points). The loop: write a document -> dataworks_validate_rule_set (every failing "
        "case with each signal's verdict) -> dataworks_preview_rule_set on a text (never "
        "stored) -> dataworks_save_rule_set (refuses anything not clean; a firing and a "
        "non-firing case per output, a case per veto; edits make version n+1) -> "
        "dataworks_get_rule_set (its sha256 is the pin) -> a recipe step rule_set@1 with "
        "rule_set_name, rule_set_version, rule_set_sha256, use (label, veto, screen, "
        "decoy_check, conflict_scan) and text_column -> dataworks_build_version; the build "
        "records each use. dataworks_plan_recipe_upgrade shows a newer version and the "
        "difference; nothing moves until the recipe is saved. dataworks_clone_rule_set copies a "
        "rule set to another behaviour key with JSON Pointer substitutions; "
        "dataworks_get_rule_set_difference compares two versions; "
        "dataworks_archive_rule_set hides one (dataworks_unarchive_rule_set). Derived rules: "
        "dataworks_derive_rule_set (a job) proposes literals from a labelled version, measured "
        "on grouped held-out rows only; dataworks_get_rule_derivation reads the figures (or not "
        "measured) and the draft, which dataworks_save_rule_set saves with its provenance; a "
        "version built with it inherits the source's visibility. Every answer carries next: the "
        "tool to call after it."
    ),
    "code_rules": (
        "Code rules (feature 018) let the operator's own Python decide a fact a declarative rule "
        "cannot (a checksum, a table lookup), run ONLY inside an isolated runner: a WebAssembly "
        "guest with no files, no network, no environment, fuel and a memory cap, one fresh "
        "instance per text. A code rule can only report a COUNT per text, never text; a call "
        "that fails reads a reason (rule_resource_limit, rule_timeout, rule_error, "
        "rule_output_invalid, rule_input_too_large, rule_load_refused, rule_worker_unavailable, "
        "rule_not_approved), never 'did not fire'. The loop: dataworks_get_runner_contract (the "
        "modules you may import, the limits, the reasons) -> write a document (runner_contract, "
        "source, functions, params_schema, derived_from) and its cases (a firing and a "
        "non-firing case per function) -> dataworks_test_code_rule (a dry run on the cases and "
        "your own texts; an error on your own text shows its type, line and a short message; "
        "nothing is stored; rate-limited) -> dataworks_save_code_rule. A save runs every case "
        "twice at the contract floor first and refuses anything not clean, naming the item. A "
        "document whose SHA-256 the operator has not approved WAITS for the operator: you get a "
        "pending approval (poll dataworks_get_approval_status) and the operator reads the source "
        "and its case results before anything is stored. Editing only the cases keeps the hash "
        "and needs no new approval; any change to the source, functions, schema or derived_from "
        "does. dataworks_list_code_rules and dataworks_get_code_rule show the approval state and "
        "whether a rule still passes under the runner's current contract version; "
        "dataworks_archive_code_rule hides one (dataworks_unarchive_code_rule). Only the operator "
        "approves or revokes a hash, on the Code rules screen: no tool does. Code rules are off "
        "until the operator switches them on (CODE_RULES_ENABLED). Every answer carries next."
    ),
    "behaviour_dataset": (
        "A behaviour detector dataset (feature 017) is built the same way for any behaviour: an "
        "identifier such as a card number, a routing number or an account number, or a behaviour "
        "that is not an identifier at all. A behaviour is data, never code: its key (the slug "
        "shared with miStudio's behaviour catalogue) goes on every object you make for it. Start "
        "with dataworks_get_behaviour_status(behaviour_key): it lists the behaviour's objects "
        "found by that key, what each last said, the ordered journey (each step with the tool "
        "to call), the next missing step and every approval still ahead. Call it again after "
        "each step; it reads only. The journey, in short: resolve the definition, save the rule "
        "set, save the generation templates and recipes, build the carrier plan, generate "
        "carriers per generator, build the labelled version with its value-directed held-out "
        "split and proof, score a stratified sample with a second opinion, read the agreement "
        "by stratum, send the disagreements to review, create the main and sub-question sets, "
        "check them and send them. Every list takes page, limit and behaviour_key. To start "
        "another behaviour from a proven one, clone its recipes, templates and rule sets with "
        "substitutions and read dataworks_get_clone_difference. A large table (a census, an "
        "agreement by stratum, a proof's values) comes back worst first with a page token; "
        "dataworks_get_result_download_url gives the whole table as a file. Approvals are "
        "the operator's; a gated call returns a pending approval to poll."
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
            Literal[
                "overview",
                "results",
                "approvals",
                "jobs",
                "secrets",
                "data_designer",
                "identifiers",
                "rule_signals",
                "code_rules",
                "behaviour_dataset",
            ]
            | None,
            Field(description="One topic; omit for the list of topics with the overview."),
        ] = None,
    ) -> Any:
        """Guidance for agents: the workflow, the three result shapes (resource, pending approval,
        unavailable), the approval actions, jobs and secrets. Call this first. Makes no
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
        limit: Annotated[
            int, Field(description="At most this many, newest first.", ge=1, le=500)
        ] = 50,
        detail: Annotated[
            Literal["summary", "full"],
            Field(description="summary (default) leaves out each job's params and result."),
        ] = "summary",
        page: Page = 1,
    ) -> Any:
        """List jobs (imports, builds, publishes, exports) with state and progress, newest
        first. A build's message names its current step and whether it is queued for a worker,
        running, or waiting on another build."""
        return await gated_call(
            ctx.gate,
            READ,
            lambda: client.get(
                "/jobs", state=state, kind=kind, limit=limit, detail=detail, page=page
            ),
        )

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
        include_archived: IncludeArchived = False,
        page: Page = 1,
        limit: Limit = 50,
    ) -> Any:
        """List approval requests: action, who asked, status, expiry, and the result once run.
        Secret values are never included. Archived approvals are left out unless
        include_archived is true. One page, newest first, with the total."""
        return await gated_call(
            ctx.gate,
            READ,
            lambda: client.get(
                "/approvals",
                status=status,
                include_archived=include_archived,
                page=page,
                limit=limit,
            ),
        )

    @mcp.tool()
    async def dataworks_get_approval_status(
        approval_id: Annotated[str, Field(description="Approval ID, apr_...", pattern=ID_PATTERN)],
    ) -> Any:
        """One approval: pending, executing, executed (with result_kind and result_id), failed
        (with error), rejected (with reason) or expired. Poll this after a tool returned a
        pending approval."""
        return await gated_call(ctx.gate, READ, lambda: client.get(f"/approvals/{approval_id}"))

    @mcp.tool()
    async def dataworks_archive_approval(
        approval_id: Annotated[str, Field(description="Approval ID, apr_...", pattern=ID_PATTERN)],
        reason: ArchiveReason = None,
    ) -> Any:
        """Archive a rejected or expired approval: hide it from the default list. Archiving
        decides nothing and deletes nothing; dataworks_unarchive_approval restores it. Refused
        (approval_not_archivable) for any other status."""
        return await gated_call(
            ctx.gate,
            READ,
            lambda: client.post(
                f"/approvals/{approval_id}/archive", json_body=archive_body(reason)
            ),
        )

    @mcp.tool()
    async def dataworks_unarchive_approval(
        approval_id: Annotated[str, Field(description="Approval ID, apr_...", pattern=ID_PATTERN)],
        reason: ArchiveReason = None,
    ) -> Any:
        """Unarchive an approval: list it again."""
        return await gated_call(
            ctx.gate,
            READ,
            lambda: client.post(
                f"/approvals/{approval_id}/unarchive", json_body=archive_body(reason)
            ),
        )
