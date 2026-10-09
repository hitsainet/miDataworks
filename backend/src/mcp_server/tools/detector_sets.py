"""Category ``detector_sets``: feature 009's detector sets, sends to miStudio and probe results
(009 FR-009.72 - FR-009.76; 010 FTID sections 3.7, 3.8).

``dataworks_send_detector_set`` ALWAYS waits for the operator when an agent calls it (``hub_push``,
P-06): one approval covers the whole send. ``dataworks_create_reproduction_link`` waits too
(``gate_target_write``): a link sets the target the reproduction gate compares against, as a
calibration gate target does (S3-08), while a reward mark or an agreement report only records.
Every other tool runs at once. A reward-marked probe
never appears among a result's evaluations (FR-009.70).

``POST /detector-sends/{id}/cancel`` has no tool: ``dataworks_cancel_job`` reaches the same
cancellation. ``dataworks_run_detector_operator`` is still pending: it waits for 009's detector
operators (M4), and stays in ``tests/support/mcp_ledger.py``'s pending tools until they register.

Tools reach the backend over HTTP only (ADR-016; ``test_mcp_tools_stay_on_http``).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from ..client import DataworksClient
from ..context import FILES, ID_PATTERN, JOB, JOB_FILES, READ, Limit, Page, ToolContext
from ..health_gate import gated_call

SetId = Annotated[str, Field(description="Detector set ID, dts_...", pattern=ID_PATTERN)]
SendId = Annotated[str, Field(description="Detector-set send ID, dsn_...", pattern=ID_PATTERN)]
SetName = Annotated[
    str,
    Field(description="Lower case, digits and hyphens.", pattern=r"^[a-z0-9][a-z0-9-]{0,99}$"),
]
Roles = Annotated[
    list[dict[str, Any]],
    Field(
        description=(
            "Roles: each {role: train|id_test|ood_eval|calibration_negatives, version_id, split, "
            "input_column, label_column, label_mapping: {value: positive|negative|excluded}, "
            "pair_column?, label_source_columns? (columns the label was computed from; D-3's "
            "shortcut audit excludes and reports them), negatives_basis? (required for "
            "calibration_negatives: {kind: labeler_filtered, labeler_identity_hash, rule} | "
            "{kind: human_labelled, label_column, negative_values, labelled_by, rule} | "
            "{kind: assumed_negative}), display_name?}. "
            "Exactly one train, id_test and calibration_negatives; one or more ood_eval. "
            'A null label value is mapped by the key "None".'
        ),
        max_length=32,
    ),
]
MonitoredRef = Annotated[
    dict[str, Any] | None,
    Field(
        description=(
            "The text the probe will monitor: {kind: role, role_index} or {kind: version, "
            "version_id, split, column}. Default: the first out-of-distribution role."
        )
    ),
]


# --- minimal pairs (types and body; the tools are registered in ``register``) ---------------

ChainId = Annotated[str, Field(description="Minimal-pair chain ID, mpc_...", pattern=ID_PATTERN)]


def _chain_body(
    input_version_id: str,
    text_column: str,
    seed_splits: list[str],
    sample_size: int,
    respond_template_id: str,
    rubric_id: str,
    flip_from: str,
    flip_to: str,
    seed: int | None,
    generator_setting: dict[str, Any] | None,
    judge_field: str | None,
    judge_seed: int | None,
    max_edit_chars: int | None,
    max_edit_words: int | None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "input_version_id": input_version_id,
        "text_column": text_column,
        "seed_splits": seed_splits,
        "sample_size": sample_size,
        "respond_template_id": respond_template_id,
        "rubric_id": rubric_id,
        "flip_from": flip_from,
        "flip_to": flip_to,
        "seed": seed,
        "generator_setting": generator_setting,
        "judge_field": judge_field,
        "judge_seed": judge_seed,
        "max_edit_chars": max_edit_chars,
        "max_edit_words": max_edit_words,
    }
    return {k: v for k, v in body.items() if v is not None}


VersionArg = Annotated[str, Field(description="Version holding the seed rows.", pattern=ID_PATTERN)]
TextColumn = Annotated[
    str, Field(description="The text column each seed is edited in.", min_length=1)
]
SeedSplits = Annotated[
    list[str], Field(description="Splits to draw seeds from (never held out).", min_length=1)
]
SampleSize = Annotated[int, Field(description="Seed rows to edit.", ge=1, le=100_000)]
Template = Annotated[
    str,
    Field(
        description="A respond template asking for the minimal flip (clone minimal-pair-v1).",
        pattern=ID_PATTERN,
    ),
]
RubricArg = Annotated[
    str, Field(description="The judge's rubric, pinned by ID.", pattern=ID_PATTERN)
]
FlipFrom = Annotated[str, Field(description="The verdict the judge must give the seed.")]
FlipTo = Annotated[str, Field(description="The verdict the judge must give the counterpart.")]
OptSeed = Annotated[int | None, Field(description="Seed for the seed draw.", ge=0)]
Generator = Annotated[
    dict[str, Any] | None,
    Field(description='Generator steering: {"kind": "none"} (default), a profile or inline.'),
]
JudgeField = Annotated[
    str | None, Field(description="Rubric input field for the text, when it has several.")
]
JudgeSeed = Annotated[int | None, Field(description="Sampling seed for the judge.", ge=0)]
MaxChars = Annotated[int | None, Field(description="Largest edit, in characters.", ge=1)]
MaxWords = Annotated[int | None, Field(description="Largest edit, in words.", ge=1)]


def register(mcp: MCPServer, client: DataworksClient, ctx: ToolContext) -> None:
    gate = ctx.gate

    @mcp.tool()
    async def dataworks_create_detector_set(
        name: SetName,
        roles: Roles,
        description: Annotated[
            str, Field(description="What the set is for.", max_length=4000)
        ] = "",
        positive_meaning: Annotated[
            str, Field(description="What the positive class means, in words.", max_length=1000)
        ] = "",
        monitored_ref: MonitoredRef = None,
    ) -> Any:
        """Create a detector set: training rows, an in-distribution test, out-of-distribution
        evaluations and calibration negatives bound to version splits. Run
        dataworks_check_detector_set before sending."""
        body = {
            "name": name,
            "description": description,
            "positive_meaning": positive_meaning,
            "roles": roles,
            "monitored_ref": monitored_ref,
        }
        return await gated_call(gate, FILES, lambda: client.post("/detector-sets", json_body=body))

    @mcp.tool()
    async def dataworks_list_detector_sets(
        archived: Annotated[bool | None, Field(description="Only archived, or only live.")] = None,
        page: Page = 1,
        limit: Limit = 50,
    ) -> Any:
        """Detector sets with their role summary, last send state and last result rung."""
        return await gated_call(
            gate,
            READ,
            lambda: client.get("/detector-sets", archived=archived, page=page, limit=limit),
        )

    @mcp.tool()
    async def dataworks_get_detector_set(set_id: SetId) -> Any:
        """One detector set: roles, length profiles, sends."""
        return await gated_call(gate, READ, lambda: client.get(f"/detector-sets/{set_id}"))

    @mcp.tool()
    async def dataworks_update_detector_set(
        set_id: SetId,
        description: Annotated[
            str | None, Field(description="New description.", max_length=4000)
        ] = None,
        positive_meaning: Annotated[
            str | None, Field(description="New meaning of the positive class.", max_length=1000)
        ] = None,
        roles: Annotated[
            list[dict[str, Any]] | None,
            Field(description="Replaces every role (same shape as create).", max_length=32),
        ] = None,
        monitored_ref: MonitoredRef = None,
    ) -> Any:
        """Change a set. Earlier sends keep the snapshot of what they sent."""
        body = {
            k: v
            for k, v in {
                "description": description,
                "positive_meaning": positive_meaning,
                "roles": roles,
                "monitored_ref": monitored_ref,
            }.items()
            if v is not None
        }
        return await gated_call(
            gate, FILES, lambda: client.patch(f"/detector-sets/{set_id}", json_body=body)
        )

    @mcp.tool()
    async def dataworks_archive_detector_set(set_id: SetId) -> Any:
        """Archive a set. A set that was sent is never deleted."""
        return await gated_call(gate, READ, lambda: client.post(f"/detector-sets/{set_id}/archive"))

    @mcp.tool()
    async def dataworks_check_detector_set(set_id: SetId) -> Any:
        """Run checks D-1 to D-8 (green, note or refused, each with a reason and next step), and
        return the label values each mapping must cover and both length profiles. A refused check
        blocks the send."""
        return await gated_call(gate, FILES, lambda: client.post(f"/detector-sets/{set_id}/checks"))

    @mcp.tool()
    async def dataworks_send_detector_set(
        set_id: SetId,
        repositories: Annotated[
            dict[str, str] | None,
            Field(
                description="version ID -> owner/name. Others default to <namespace>/<dataset>-v<n>."
            ),
        ] = None,
        namespace: Annotated[
            str | None, Field(description="Hub namespace for default repositories.", max_length=96)
        ] = None,
        visibility: Annotated[
            Literal["private", "public"], Field(description="Hub visibility; default private.")
        ] = "private",
    ) -> Any:
        """Send the set to miStudio: publish each version to the Hugging Face Hub, download it into
        miStudio and register every role. ALWAYS waits for the operator's approval when called by
        an agent (hub_push): returns {"approval_id", "status": "pending", ...}. Poll
        dataworks_get_approval_status, then dataworks_get_detector_send."""
        body = {
            "repositories": repositories or {},
            "namespace": namespace,
            "visibility": visibility,
        }
        return await gated_call(
            gate, JOB_FILES, lambda: client.post(f"/detector-sets/{set_id}/send", json_body=body)
        )

    @mcp.tool()
    async def dataworks_list_detector_sends(set_id: SetId) -> Any:
        """A set's sends, newest first."""
        return await gated_call(gate, READ, lambda: client.get(f"/detector-sets/{set_id}/sends"))

    @mcp.tool()
    async def dataworks_get_detector_send(send_id: SendId) -> Any:
        """A send's steps (publish, download, register), its checks and notes, and the miStudio
        run-request skeleton once every role is registered."""
        return await gated_call(gate, READ, lambda: client.get(f"/detector-sends/{send_id}"))

    @mcp.tool()
    async def dataworks_resume_detector_send(send_id: SendId) -> Any:
        """Resume a failed or cancelled send; recorded steps are never repeated."""
        return await gated_call(
            gate, JOB_FILES, lambda: client.post(f"/detector-sends/{send_id}/resume")
        )

    @mcp.tool()
    async def dataworks_refresh_probe_results(set_id: SetId) -> Any:
        """Read miStudio's runs, probes and reports for the set into a new snapshot: per-set AUROC
        with its interval, the rung in miStudio's words, firing rates and caveats."""
        return await gated_call(
            gate, FILES, lambda: client.post(f"/detector-sets/{set_id}/results/refresh")
        )

    @mcp.tool()
    async def dataworks_get_probe_results(
        set_id: SetId,
        snapshot: Annotated[
            str, Field(description="latest, or a snapshot ID.", pattern=ID_PATTERN)
        ] = "latest",
    ) -> Any:
        """A stored results snapshot. Probes marked as training rewards appear only under
        training_reward, never among evaluations."""
        return await gated_call(
            gate, READ, lambda: client.get(f"/detector-sets/{set_id}/results", snapshot=snapshot)
        )

    @mcp.tool()
    async def dataworks_mark_reward_probe(
        mistudio_probe_id: Annotated[
            str, Field(description="miStudio probe ID, pm_...", pattern=ID_PATTERN)
        ],
        reason: Annotated[
            str, Field(description="Why it is a reward probe.", min_length=1, max_length=2000)
        ],
    ) -> Any:
        """Mark a miStudio probe as used as a training reward. A mark is never removed; the probe
        is never shown as an evaluation again."""
        body = {"mistudio_probe_id": mistudio_probe_id, "reason": reason}
        return await gated_call(gate, READ, lambda: client.post("/reward-marks", json_body=body))

    @mcp.tool()
    async def dataworks_list_reward_marks() -> Any:
        """Every probe marked as a training reward."""
        return await gated_call(gate, READ, lambda: client.get("/reward-marks"))

    @mcp.tool()
    async def dataworks_create_reproduction_link(
        mistudio_probe_id: Annotated[
            str,
            Field(
                description="The miStudio probe (pm_...) a miLLM probe was imported from.",
                pattern=ID_PATTERN,
            ),
        ],
        probe_dataset_id: Annotated[
            str,
            Field(
                description="The miStudio view (pmd_...) miStudio evaluated it on; the plan's "
                "REPRODUCTION_UNAVAILABLE refusal lists them under link_candidates.",
                pattern=ID_PATTERN,
            ),
        ],
        version_id: Annotated[
            str, Field(description="The version holding those rows.", pattern=ID_PATTERN)
        ],
        split: Annotated[str, Field(description="Its split.", min_length=1, max_length=200)],
        input_column: Annotated[
            str | None, Field(description="Default: the view's input column.", max_length=200)
        ] = None,
        label_column: Annotated[
            str | None, Field(description="Default: the view's label column.", max_length=200)
        ] = None,
        label_mapping: Annotated[
            dict[str, Literal["positive", "negative", "excluded"]] | None,
            Field(description="{value: positive|negative|excluded}. Default: the view's."),
        ] = None,
    ) -> Any:
        """Link a version split to an evaluation miStudio already recorded for a probe, so the
        reproduction gate can run for an imported probe (FR-009.77 option (b)). Rows are checked
        now: count and class balance always, a content hash when miStudio serves the rows; rows
        that provably differ are refused. An agent call WAITS for the operator's approval
        (gate_target_write): a link sets what a gate compares against."""
        body: dict[str, Any] = {
            "mistudio_probe_id": mistudio_probe_id,
            "probe_dataset_id": probe_dataset_id,
            "version_id": version_id,
            "split": split,
            "input_column": input_column,
            "label_column": label_column,
            "label_mapping": label_mapping,
        }
        return await gated_call(
            gate, FILES, lambda: client.post("/reproduction-links", json_body=body)
        )

    @mcp.tool()
    async def dataworks_list_reproduction_links(
        mistudio_probe_id: Annotated[
            str | None, Field(description="Only links for this miStudio probe.", max_length=128)
        ] = None,
    ) -> Any:
        """Every reproduction link with its checks (content or counts only) and the runs that used
        it."""
        return await gated_call(
            gate,
            READ,
            lambda: client.get("/reproduction-links", mistudio_probe_id=mistudio_probe_id),
        )

    @mcp.tool()
    async def dataworks_get_reproduction_link(
        link_id: Annotated[str, Field(description="Link ID, rpl_...", pattern=ID_PATTERN)],
    ) -> Any:
        """One reproduction link: the miStudio figure, every check and the scoring forms."""
        return await gated_call(gate, READ, lambda: client.get(f"/reproduction-links/{link_id}"))

    @mcp.tool()
    async def dataworks_create_agreement_report(
        version_id: Annotated[
            str, Field(description="Version both runs scored.", pattern=ID_PATTERN)
        ],
        split: Annotated[str, Field(description="Split compared.", min_length=1, max_length=200)],
        probe_label_run_id: Annotated[
            str, Field(description="The probe-verdict label run.", pattern=ID_PATTERN)
        ],
        judge_label_run_id: Annotated[
            str, Field(description="The judge label run.", pattern=ID_PATTERN)
        ],
        reference: Annotated[
            dict[str, Any],
            Field(
                description=(
                    "{kind: label_column, column, positive_values, negative_values} or "
                    "{kind: label_run, label_run_id}."
                )
            ),
        ],
        training_version_id: Annotated[
            str | None,
            Field(description="Flags a judge that labelled the training rows.", pattern=ID_PATTERN),
        ] = None,
        send_disagreements_to_review: Annotated[
            bool, Field(description="Open a review queue over the disagreements.")
        ] = False,
    ) -> Any:
        """Compare a probe-verdict run with a judge run on the same rows: two AUROCs with
        intervals, agreement and kappa. Starts no miStudio judge run."""
        body = {
            "version_id": version_id,
            "split": split,
            "probe_label_run_id": probe_label_run_id,
            "judge_label_run_id": judge_label_run_id,
            "reference": reference,
            "training_version_id": training_version_id,
            "send_disagreements_to_review": send_disagreements_to_review,
        }
        return await gated_call(
            gate, FILES, lambda: client.post("/agreement-reports", json_body=body)
        )

    @mcp.tool()
    async def dataworks_get_agreement_report(
        report_id: Annotated[
            str, Field(description="Agreement report ID, agr_...", pattern=ID_PATTERN)
        ],
    ) -> Any:
        """One agreement report."""
        return await gated_call(gate, READ, lambda: client.get(f"/agreement-reports/{report_id}"))

    # --- minimal pairs as a chain (009 FR-009.60 - FR-009.64; operator decision 2026-10-07) ----

    @mcp.tool()
    async def dataworks_plan_minimal_pairs(
        input_version_id: VersionArg,
        text_column: TextColumn,
        seed_splits: SeedSplits,
        sample_size: SampleSize,
        respond_template_id: Template,
        rubric_id: RubricArg,
        flip_from: FlipFrom,
        flip_to: FlipTo,
        seed: OptSeed = None,
        generator_setting: Generator = None,
        judge_field: JudgeField = None,
        judge_seed: JudgeSeed = None,
        max_edit_chars: MaxChars = None,
        max_edit_words: MaxWords = None,
    ) -> Any:
        """Dry run of a minimal-pair chain: every refusal (held-out seeds, a judge that is the
        generator, no judge, an unknown verdict), the generation plan and the judge. Writes
        nothing."""
        body = _chain_body(
            input_version_id, text_column, seed_splits, sample_size, respond_template_id,
            rubric_id, flip_from, flip_to, seed, generator_setting, judge_field, judge_seed,
            max_edit_chars, max_edit_words,
        )  # fmt: skip
        return await gated_call(
            gate, FILES, lambda: client.post("/minimal-pair-chains/plan", json_body=body)
        )

    @mcp.tool()
    async def dataworks_start_minimal_pairs(
        input_version_id: VersionArg,
        text_column: TextColumn,
        seed_splits: SeedSplits,
        sample_size: SampleSize,
        respond_template_id: Template,
        rubric_id: RubricArg,
        flip_from: FlipFrom,
        flip_to: FlipTo,
        seed: OptSeed = None,
        generator_setting: Generator = None,
        judge_field: JudgeField = None,
        judge_seed: JudgeSeed = None,
        max_edit_chars: MaxChars = None,
        max_edit_words: MaxWords = None,
    ) -> Any:
        """Start a minimal-pair chain: a generation run edits each seed, a build keeps the pairs,
        a judge run with the pinned rubric labels them, and a build keeps only verified flips with
        a pair_id. Returns the chain with each stage's run, job and version ID."""
        body = _chain_body(
            input_version_id, text_column, seed_splits, sample_size, respond_template_id,
            rubric_id, flip_from, flip_to, seed, generator_setting, judge_field, judge_seed,
            max_edit_chars, max_edit_words,
        )  # fmt: skip
        return await gated_call(
            gate, JOB_FILES, lambda: client.post("/minimal-pair-chains", json_body=body)
        )

    @mcp.tool()
    async def dataworks_list_minimal_pair_chains(
        state: Annotated[
            Literal["running", "completed", "failed", "cancelled"] | None,
            Field(description="Only chains in this state."),
        ] = None,
        input_version_id: Annotated[
            str | None, Field(description="Only chains over this version.", pattern=ID_PATTERN)
        ] = None,
        page: Page = 1,
        limit: Limit = 50,
    ) -> Any:
        """Minimal-pair chains, newest first, each with its stages."""
        return await gated_call(
            gate,
            READ,
            lambda: client.get(
                "/minimal-pair-chains",
                state=state,
                input_version_id=input_version_id,
                page=page,
                limit=limit,
            ),
        )

    @mcp.tool()
    async def dataworks_get_minimal_pair_chain(chain_id: ChainId) -> Any:
        """One chain: its state, the stage it stands at (or failed at, and why), each stage's run,
        job and version ID, and the counts of verified and unverified pairs."""
        return await gated_call(gate, READ, lambda: client.get(f"/minimal-pair-chains/{chain_id}"))

    @mcp.tool()
    async def dataworks_resume_minimal_pair_chain(chain_id: ChainId) -> Any:
        """Resume a failed or cancelled chain at the stage that stopped."""
        return await gated_call(
            gate, JOB_FILES, lambda: client.post(f"/minimal-pair-chains/{chain_id}/resume")
        )

    @mcp.tool()
    async def dataworks_cancel_minimal_pair_chain(chain_id: ChainId) -> Any:
        """Cancel a running chain and the stage that is live."""
        return await gated_call(
            gate, JOB, lambda: client.post(f"/minimal-pair-chains/{chain_id}/cancel")
        )
