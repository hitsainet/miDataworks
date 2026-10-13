"""Category ``exports``: publishing, the handoff manifest and exports (008 FR-008.50-FR-008.53).

Gated when an agent calls: ``dataworks_publish_version`` and ``dataworks_push_card``
(``hub_push``). The three exports write local files only (``POST /exports`` carries no gate in
008); a miForge set or reward bundle reaches the Hub only through a publish, which is gated.
``POST /model-terms/{model_id}/notes`` has no tool: a note can unlock a public push (008 T16).
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
    MODEL_ID_PATTERN,
    READ,
    REPO_ID_PATTERN,
    Limit,
    Page,
    ToolContext,
)
from ..health_gate import gated_call

VersionId = Annotated[str, Field(description="Version ID, ver_...", pattern=ID_PATTERN)]
BuildId = Annotated[
    str,
    Field(description="Publish build ID from dataworks_build_version_files.", pattern=ID_PATTERN),
]
PublishId = Annotated[str, Field(description="Publish ID, pub_...", pattern=ID_PATTERN)]
HubRepo = Annotated[
    str, Field(description="Hub dataset repository, owner/name.", pattern=REPO_ID_PATTERN)
]
Visibility = Annotated[
    Literal["private", "public"],
    Field(description="Default private. A public push is refused while any check is amber."),
]
Format = Annotated[Literal["parquet", "jsonl"], Field(description="File format.")]
LabelColumn = Annotated[
    str | None,
    Field(description="Column holding the label, when the export needs one.", max_length=200),
]
ExtraColumns = Annotated[
    list[str] | None, Field(description="Extra columns to carry, at most 50.", max_length=50)
]


def register(mcp: MCPServer, client: DataworksClient, ctx: ToolContext) -> None:
    gate = ctx.gate

    @mcp.tool()
    async def dataworks_build_version_files(
        version_id: VersionId,
        label_column: LabelColumn = None,
    ) -> Any:
        """Build the files a publish uploads (Parquet, card, manifest) and their hashes. Starts a
        job; read the build with dataworks_get_publish_build."""
        return await gated_call(
            gate,
            JOB_FILES,
            lambda: client.post(
                f"/versions/{version_id}/publish-builds", json_body={"label_column": label_column}
            ),
        )

    @mcp.tool()
    async def dataworks_get_publish_build(build_id: BuildId) -> Any:
        """A publish build's state, files and hashes."""
        return await gated_call(gate, READ, lambda: client.get(f"/publish-builds/{build_id}"))

    @mcp.tool()
    async def dataworks_run_publish_checks(
        version_id: VersionId,
        build_id: BuildId,
        repo_id: HubRepo,
        visibility: Visibility = "private",
    ) -> Any:
        """Run the publish checks (licence, terms, personal data, size) for a build and target.
        Starts a job; read the result with dataworks_get_publish_checks."""
        body = {"build_id": build_id, "repo_id": repo_id, "visibility": visibility}
        return await gated_call(
            gate,
            JOB_FILES,
            lambda: client.post(f"/versions/{version_id}/publish-checks", json_body=body),
        )

    @mcp.tool()
    async def dataworks_get_publish_checks(
        check_run_id: Annotated[str, Field(description="Check run ID.", pattern=ID_PATTERN)],
    ) -> Any:
        """A publish-check run: each check green, amber or red, with the reason."""
        return await gated_call(
            gate, READ, lambda: client.get(f"/publish-check-runs/{check_run_id}")
        )

    @mcp.tool()
    async def dataworks_get_card_draft(
        version_id: VersionId,
        repo_id: Annotated[
            str | None, Field(description="Target repository, owner/name.", pattern=REPO_ID_PATTERN)
        ] = None,
        build_id: Annotated[
            str | None, Field(description="Publish build ID.", pattern=ID_PATTERN)
        ] = None,
    ) -> Any:
        """The dataset card a publish would push, generated from the version's provenance."""
        return await gated_call(
            gate,
            READ,
            lambda: client.get(
                f"/versions/{version_id}/card-draft", repo_id=repo_id, build_id=build_id
            ),
        )

    @mcp.tool()
    async def dataworks_publish_version(
        version_id: VersionId,
        build_id: BuildId,
        repo_id: HubRepo,
        visibility: Visibility = "private",
        card_prose: Annotated[
            str, Field(description="Prose added to the generated card.", max_length=20000)
        ] = "",
    ) -> Any:
        """Publish a version's build to the Hugging Face (HF) Hub. ALWAYS waits for the
        operator's approval when called by an agent (hub_push): returns {"approval_id",
        "status": "pending", ...}. Poll dataworks_get_approval_status."""
        body = {
            "version_id": version_id,
            "build_id": build_id,
            "repo_id": repo_id,
            "visibility": visibility,
            "card_prose": card_prose,
        }
        return await gated_call(gate, JOB_FILES, lambda: client.post("/publishes", json_body=body))

    @mcp.tool()
    async def dataworks_push_card(
        publish_id: PublishId,
        card_prose: Annotated[
            str, Field(description="The new card prose.", min_length=1, max_length=20000)
        ],
    ) -> Any:
        """Push an updated dataset card to a published repository. ALWAYS waits for the
        operator's approval when called by an agent (hub_push)."""
        return await gated_call(
            gate,
            JOB,
            lambda: client.post(
                f"/publishes/{publish_id}/card", json_body={"card_prose": card_prose}
            ),
        )

    @mcp.tool()
    async def dataworks_verify_hub_hashes(publish_id: PublishId) -> Any:
        """Re-read the published files from the Hub and compare their hashes with the build.
        Starts a job."""
        return await gated_call(gate, JOB, lambda: client.post(f"/publishes/{publish_id}/reverify"))

    @mcp.tool()
    async def dataworks_list_publishes(
        version_id: Annotated[
            str | None, Field(description="Filter by version.", pattern=ID_PATTERN)
        ] = None,
        repo_id: Annotated[
            str | None, Field(description="Filter by repository.", pattern=REPO_ID_PATTERN)
        ] = None,
        page: Page = 1,
        limit: Limit = 50,
    ) -> Any:
        """List publishes with state and verification result."""
        return await gated_call(
            gate,
            READ,
            lambda: client.get(
                "/publishes", version_id=version_id, repo_id=repo_id, page=page, limit=limit
            ),
        )

    @mcp.tool()
    async def dataworks_get_publish(publish_id: PublishId) -> Any:
        """One publish: repository, commit, files, hashes, verification."""
        return await gated_call(gate, READ, lambda: client.get(f"/publishes/{publish_id}"))

    @mcp.tool()
    async def dataworks_get_version_manifest(version_id: VersionId) -> Any:
        """The handoff manifest (midataworks.dataset-version/v1) that miStudio and miForge read."""
        return await gated_call(
            gate, FILES, lambda: client.get(f"/versions/{version_id}/handoff-manifest")
        )

    @mcp.tool()
    async def dataworks_export_trl(
        version_id: VersionId,
        trl_type: Annotated[
            Literal["sft", "dpo", "kto", "grpo_prompt", "prm"] | None,
            Field(description="TRL (Transformer Reinforcement Learning) dataset type."),
        ] = None,
        format: Format = "parquet",
        label_column: LabelColumn = None,
        extra_columns: ExtraColumns = None,
    ) -> Any:
        """Export a version as a TRL training set (local files). Starts a job."""
        body = {
            "target": "trl",
            "version_id": version_id,
            "trl_type": trl_type,
            "format": format,
            "label_column": label_column,
            "extra_columns": extra_columns or [],
        }
        return await gated_call(gate, JOB_FILES, lambda: client.post("/exports", json_body=body))

    @mcp.tool()
    async def dataworks_export_miforge_set(
        version_id: VersionId,
        miforge_set_kind: Annotated[
            Literal["prompt_set", "corpus", "preference_pairs", "test_set", "retention_set"],
            Field(description="The miForge set kind."),
        ],
        format: Format = "parquet",
        label_column: LabelColumn = None,
        extra_columns: ExtraColumns = None,
    ) -> Any:
        """Export a version as a miForge set (local files). Starts a job. Publishing it to the
        Hub is a separate, gated publish."""
        body = {
            "target": "miforge_set",
            "version_id": version_id,
            "miforge_set_kind": miforge_set_kind,
            "format": format,
            "label_column": label_column,
            "extra_columns": extra_columns or [],
        }
        return await gated_call(gate, JOB_FILES, lambda: client.post("/exports", json_body=body))

    @mcp.tool()
    async def dataworks_export_reward_bundle(
        version_id: VersionId,
        format: Format = "parquet",
        label_column: LabelColumn = None,
        extra_columns: ExtraColumns = None,
    ) -> Any:
        """Export a version as a reward bundle (local files). Starts a job."""
        body = {
            "target": "reward_bundle",
            "version_id": version_id,
            "format": format,
            "label_column": label_column,
            "extra_columns": extra_columns or [],
        }
        return await gated_call(gate, JOB_FILES, lambda: client.post("/exports", json_body=body))

    @mcp.tool()
    async def dataworks_list_exports(
        version_id: Annotated[
            str | None, Field(description="Filter by version.", pattern=ID_PATTERN)
        ] = None,
        page: Page = 1,
        limit: Limit = 50,
    ) -> Any:
        """List exports with state and files."""
        return await gated_call(
            gate,
            READ,
            lambda: client.get("/exports", version_id=version_id, page=page, limit=limit),
        )

    @mcp.tool()
    async def dataworks_get_export(
        export_id: Annotated[str, Field(description="Export ID.", pattern=ID_PATTERN)],
    ) -> Any:
        """One export: target, state, files and manifest hash."""
        return await gated_call(gate, READ, lambda: client.get(f"/exports/{export_id}"))

    @mcp.tool()
    async def dataworks_get_licence_table() -> Any:
        """The licence table: which licences permit a public push, private only, or forbid."""
        return await gated_call(gate, READ, lambda: client.get("/licence-table"))

    @mcp.tool()
    async def dataworks_get_model_terms(
        model_id: Annotated[
            str, Field(description="Model ID, for example owner/name.", pattern=MODEL_ID_PATTERN)
        ],
    ) -> Any:
        """A model's terms notes: whether training on its outputs is permitted."""
        return await gated_call(gate, READ, lambda: client.get(f"/model-terms/{model_id}"))

    @mcp.tool()
    async def dataworks_save_config_version(
        kind: Annotated[Literal["selector", "grader"], Field(description="Config kind.")],
        name: Annotated[
            str,
            Field(
                description="Lower-case name with hyphens.", pattern=r"^[a-z0-9][a-z0-9-]{0,99}$"
            ),
        ],
        body: Annotated[dict[str, Any], Field(description="The configuration body.")],
        parent_id: Annotated[
            str | None, Field(description="Earlier version this one revises.", pattern=ID_PATTERN)
        ] = None,
    ) -> Any:
        """Save a new numbered config version (a selector or grader for miForge sets)."""
        payload = {"kind": kind, "name": name, "body": body, "parent_id": parent_id}
        return await gated_call(
            gate, READ, lambda: client.post("/config-versions", json_body=payload)
        )

    @mcp.tool()
    async def dataworks_list_config_versions(
        kind: Annotated[
            Literal["selector", "grader"] | None, Field(description="Filter by kind.")
        ] = None,
    ) -> Any:
        """List config versions."""
        return await gated_call(gate, READ, lambda: client.get("/config-versions", kind=kind))

    @mcp.tool()
    async def dataworks_get_config_version(
        config_id: Annotated[str, Field(description="Config version ID.", pattern=ID_PATTERN)],
    ) -> Any:
        """One config version and its body."""
        return await gated_call(gate, READ, lambda: client.get(f"/config-versions/{config_id}"))
