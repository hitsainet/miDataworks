"""Category ``datasets``: sources (001) and datasets, versions and recipes (002).

Feature 001 routes (FR-001.34, FR-001.38) and feature 002 routes (FR-002.45, FR-002.37,
FR-002.38), each read from the live OpenAPI document (010 FTID section 3.8). Gated, when an agent
calls: ``dataworks_delete_version`` (``version_delete``), ``dataworks_annotate_source``
(``source_annotate``), ``dataworks_build_version`` (``agent_label_rows`` over the window), and the
HF preview and import when they carry a token (``secret_write``).
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from ..client import DataworksClient
from ..context import (
    FILES,
    ID_PATTERN,
    JOB,
    JOB_FILES,
    READ,
    REPO_ID_PATTERN,
    Limit,
    Page,
    ToolContext,
)
from ..health_gate import gated_call

#: The largest upload an agent may send through MCP, decoded (the MCP Ingress body limit, as
#: miStudio's `proxy-body-size: 10m`). Larger files are uploaded in the UI (FTID IQ7).
UPLOAD_MAX_BYTES = 10 * 1024 * 1024

TargetType = Literal["sft", "dpo", "kto", "grpo_prompt", "prm", "detector", "untyped"]
SourceId = Annotated[str, Field(description="Source ID, src_...", pattern=ID_PATTERN)]
DatasetId = Annotated[str, Field(description="Dataset ID, ds_...", pattern=ID_PATTERN)]
VersionId = Annotated[str, Field(description="Version ID, ver_...", pattern=ID_PATTERN)]
RecipeId = Annotated[str, Field(description="Recipe ID, rcp_...", pattern=ID_PATTERN)]
HfRepo = Annotated[
    str,
    Field(description="Hugging Face (HF) dataset repository, owner/name.", pattern=REPO_ID_PATTERN),
]
AccessToken = Annotated[
    str | None,
    Field(
        description=(
            "Optional HF access token for a gated dataset, used for this request only and never "
            "stored. Sending one makes the call wait for the operator's approval (secret_write)."
        ),
        max_length=512,
    ),
]


def decode_upload(content_base64: str) -> bytes:
    """The decoded file, refused above 10 MiB or when not base64 (before any request).

    Refusals are ``ToolError``: mcp 2.x shows the agent a tool's message only for that type.
    """
    if len(content_base64) > (UPLOAD_MAX_BYTES * 4) // 3 + 8:
        raise ToolError("The file is over the 10 MiB MCP upload cap; upload it in the UI.")
    try:
        raw = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ToolError("content_base64 is not valid base64.") from exc
    if len(raw) > UPLOAD_MAX_BYTES:
        raise ToolError("The file is over the 10 MiB MCP upload cap; upload it in the UI.")
    return raw


def register(mcp: MCPServer, client: DataworksClient, ctx: ToolContext) -> None:
    gate = ctx.gate

    # ---------------------------------------------------------------- 001 sources

    @mcp.tool()
    async def dataworks_preview_hf_dataset(
        repo_id: HfRepo,
        config: Annotated[
            str | None, Field(description="Dataset configuration (subset) name.")
        ] = None,
        split: Annotated[
            str | None, Field(description="Split to preview, for example train.")
        ] = None,
        revision: Annotated[
            str | None, Field(description="Branch, tag or commit; default the main branch.")
        ] = None,
        access_token: AccessToken = None,
    ) -> Any:
        """Preview an HF dataset before importing it: configurations, splits, columns, sample
        rows, licence and size. With an access_token the call waits for the operator's approval
        and returns {"approval_id", "status": "pending", ...}."""
        body = {
            "repo_id": repo_id,
            "config": config,
            "split": split,
            "revision": revision,
            "access_token": access_token,
        }
        return await gated_call(
            gate, JOB, lambda: client.post("/sources/hf/preview", json_body=body)
        )

    @mcp.tool()
    async def dataworks_import_hf_dataset(
        repo_id: HfRepo,
        config: Annotated[
            str | None, Field(description="Dataset configuration (subset) name.")
        ] = None,
        split: Annotated[str | None, Field(description="One split; omit for every split.")] = None,
        revision: Annotated[
            str | None, Field(description="Branch, tag or commit; pinned to a commit on import.")
        ] = None,
        access_token: AccessToken = None,
        confirm_large: Annotated[
            bool, Field(description="Confirm an import above the large-import threshold.")
        ] = False,
    ) -> Any:
        """Import an HF dataset as a source. Starts a job (poll dataworks_job_status). With an
        access_token the call waits for the operator's approval."""
        body = {
            "repo_id": repo_id,
            "config": config,
            "split": split,
            "revision": revision,
            "access_token": access_token,
            "confirm_large": confirm_large,
        }
        return await gated_call(gate, JOB_FILES, lambda: client.post("/sources/hf", json_body=body))

    @mcp.tool()
    async def dataworks_upload_file(
        filename: Annotated[
            str,
            Field(
                description="File name ending .parquet, .jsonl or .csv.",
                min_length=1,
                max_length=255,
                pattern=r"^[^/\\]+$",
            ),
        ],
        content_base64: Annotated[
            str, Field(description="The file's bytes, base64-encoded; at most 10 MiB decoded.")
        ],
        split: Annotated[
            str, Field(description="Split name for this file.", min_length=1, max_length=64)
        ] = "train",
        display_name: Annotated[
            str | None, Field(description="Name shown for the source.", max_length=200)
        ] = None,
        csv: Annotated[
            dict[str, Any] | None,
            Field(description="CSV reading options (delimiter, header), CSV files only."),
        ] = None,
    ) -> Any:
        """Upload one Parquet, JSONL or CSV file (at most 10 MiB, the MCP request limit) as a
        source. Larger files are uploaded in the UI. Starts a job."""
        raw = decode_upload(content_base64)
        manifest: dict[str, Any] = {"files": [{"name": filename, "split": split}]}
        if csv is not None:
            manifest["csv"] = csv
        if display_name is not None:
            manifest["display_name"] = display_name
        return await gated_call(
            gate,
            JOB_FILES,
            lambda: client.post_multipart(
                "/sources/uploads",
                files=[("files", (filename, raw, "application/octet-stream"))],
                data={"manifest": json.dumps(manifest, sort_keys=True)},
            ),
        )

    @mcp.tool()
    async def dataworks_list_sources(
        kind: Annotated[
            Literal["hf", "upload"] | None, Field(description="Filter by kind.")
        ] = None,
        state: Annotated[
            Literal["importing", "ready", "failed", "cancelled", "deleted"] | None,
            Field(description="Filter by state."),
        ] = None,
        page: Page = 1,
        limit: Limit = 50,
    ) -> Any:
        """List imported sources with state, licence and size."""
        return await gated_call(
            gate,
            READ,
            lambda: client.get("/sources", kind=kind, state=state, page=page, limit=limit),
        )

    @mcp.tool()
    async def dataworks_get_source(source_id: SourceId) -> Any:
        """One source: origin, pinned revision, splits, columns, licence, annotations."""
        return await gated_call(gate, READ, lambda: client.get(f"/sources/{source_id}"))

    @mcp.tool()
    async def dataworks_get_source_rows(
        source_id: SourceId,
        split: Annotated[str | None, Field(description="Split to read.", max_length=128)] = None,
        page: Page = 1,
        limit: Limit = 50,
    ) -> Any:
        """Read a page of a source's rows."""
        return await gated_call(
            gate,
            FILES,
            lambda: client.get(f"/sources/{source_id}/rows", split=split, page=page, limit=limit),
        )

    @mcp.tool()
    async def dataworks_annotate_source(
        source_id: SourceId,
        kind: Annotated[
            Literal["terms", "licence", "detection_override"],
            Field(description="What the annotation states."),
        ],
        reason: Annotated[
            str, Field(description="Why; shown to the operator.", min_length=1, max_length=2000)
        ],
        redistribution: Annotated[
            Literal["permits", "private_only", "forbids"] | None,
            Field(description="For a licence annotation: what the licence allows."),
        ] = None,
        value: Annotated[
            dict[str, Any] | None, Field(description="The annotation's content.")
        ] = None,
    ) -> Any:
        """Annotate a source's licence, terms or detection result. ALWAYS waits for the
        operator's approval when called by an agent (source_annotate, S3-01): an annotation can
        unlock a public push. Returns {"approval_id", "status": "pending", ...}."""
        body = {
            "kind": kind,
            "redistribution": redistribution,
            "value": value or {},
            "reason": reason,
        }
        return await gated_call(
            gate, READ, lambda: client.post(f"/sources/{source_id}/annotations", json_body=body)
        )

    @mcp.tool()
    async def dataworks_delete_source(
        source_id: SourceId,
        reason: Annotated[
            str, Field(description="Why it is deleted; recorded.", min_length=1, max_length=2000)
        ],
    ) -> Any:
        """Delete a source and its files. Refused while a version still uses it."""
        return await gated_call(
            gate,
            FILES,
            lambda: client.delete(f"/sources/{source_id}", json_body={"reason": reason}),
        )

    @mcp.tool()
    async def dataworks_get_source_meta() -> Any:
        """Source limits and options: upload cap, large-import threshold, accepted formats."""
        return await gated_call(gate, READ, lambda: client.get("/sources/meta"))

    # ---------------------------------------------------------------- 002 datasets

    @mcp.tool()
    async def dataworks_get_dataset_meta() -> Any:
        """Dataset options: target types and their descriptions."""
        return await gated_call(gate, READ, lambda: client.get("/datasets/meta"))

    @mcp.tool()
    async def dataworks_list_datasets(
        q: Annotated[str | None, Field(description="Search the name.", max_length=100)] = None,
        target_type: Annotated[
            TargetType | None, Field(description="Filter by target type.")
        ] = None,
        page: Page = 1,
        limit: Limit = 50,
    ) -> Any:
        """List datasets with their latest version."""
        return await gated_call(
            gate,
            READ,
            lambda: client.get("/datasets", q=q, target_type=target_type, page=page, limit=limit),
        )

    @mcp.tool()
    async def dataworks_create_dataset(
        name: Annotated[str, Field(description="Dataset name.", min_length=1, max_length=200)],
        target_type: Annotated[
            TargetType, Field(description="What the dataset trains: sft, dpo, kto, ... or untyped.")
        ] = "untyped",
        description: Annotated[str | None, Field(description="Free text.", max_length=4000)] = None,
    ) -> Any:
        """Create an empty dataset. Versions are built into it with dataworks_build_version."""
        body = {"name": name, "target_type": target_type, "description": description}
        return await gated_call(gate, READ, lambda: client.post("/datasets", json_body=body))

    @mcp.tool()
    async def dataworks_get_dataset(dataset_id: DatasetId) -> Any:
        """One dataset with its versions."""
        return await gated_call(gate, READ, lambda: client.get(f"/datasets/{dataset_id}"))

    @mcp.tool()
    async def dataworks_update_dataset(
        dataset_id: DatasetId,
        description: Annotated[
            str | None, Field(description="New description; an empty string clears it.")
        ] = None,
        target_type: Annotated[TargetType | None, Field(description="New target type.")] = None,
    ) -> Any:
        """Change a dataset's description or target type. Only the fields given are sent."""
        body = {
            k: v
            for k, v in {"description": description, "target_type": target_type}.items()
            if v is not None
        }
        if not body:
            raise ToolError("Give a description or a target_type to change.")
        return await gated_call(
            gate, READ, lambda: client.patch(f"/datasets/{dataset_id}", json_body=body)
        )

    # ---------------------------------------------------------------- 002 versions

    @mcp.tool()
    async def dataworks_build_version(
        dataset_id: DatasetId,
        recipe_revision_id: Annotated[
            str, Field(description="Recipe revision to build, rrv_...", pattern=ID_PATTERN)
        ],
        inputs: Annotated[
            list[dict[str, Any]],
            Field(
                description=(
                    'Inputs, 1 to 16: {"kind": "source", "source_id": ...} or '
                    '{"kind": "version", "version_id": ...}.'
                ),
                min_length=1,
                max_length=16,
            ),
        ],
        seed: Annotated[int | None, Field(description="Seed for any sampling step.", ge=0)] = None,
        bindings: Annotated[
            list[dict[str, Any]] | None,
            Field(description='Run bindings: {"kind": "label_run" | "generation_run", "id": ...}.'),
        ] = None,
        column_roles: Annotated[
            dict[str, Literal["content", "metadata"]] | None,
            Field(description="Per-column role: content or metadata."),
        ] = None,
    ) -> Any:
        """Build a new immutable version from a recipe revision and inputs. Starts a job. When
        the recipe labels rows and the agent total for that data would pass 5,000 rows in 24 h,
        the call waits for the operator's approval (agent_label_rows)."""
        body = {
            "dataset_id": dataset_id,
            "recipe_revision_id": recipe_revision_id,
            "inputs": inputs,
            "seed": seed,
            "bindings": bindings or [],
            "column_roles": column_roles or {},
        }
        return await gated_call(gate, JOB_FILES, lambda: client.post("/versions", json_body=body))

    @mcp.tool()
    async def dataworks_list_versions(
        dataset_id: Annotated[
            str | None, Field(description="Filter by dataset ID.", pattern=ID_PATTERN)
        ] = None,
        state: Annotated[
            Literal["completed", "deleted"] | None, Field(description="Filter by state.")
        ] = None,
        page: Page = 1,
        limit: Limit = 50,
    ) -> Any:
        """List versions."""
        return await gated_call(
            gate,
            READ,
            lambda: client.get(
                "/versions", dataset_id=dataset_id, state=state, page=page, limit=limit
            ),
        )

    @mcp.tool()
    async def dataworks_get_version(version_id: VersionId) -> Any:
        """One version: recipe hash, inputs, splits, row counts, state."""
        return await gated_call(gate, READ, lambda: client.get(f"/versions/{version_id}"))

    @mcp.tool()
    async def dataworks_get_build_manifest(version_id: VersionId) -> Any:
        """The version's internal build manifest (steps, operator versions, counts). Not the
        handoff document; that is dataworks_get_version_manifest."""
        return await gated_call(gate, FILES, lambda: client.get(f"/versions/{version_id}/manifest"))

    @mcp.tool()
    async def dataworks_get_version_rows(
        version_id: VersionId,
        split: Annotated[str | None, Field(description="Split to read.", max_length=64)] = None,
        q: Annotated[str | None, Field(description="Text search.", max_length=200)] = None,
        where: Annotated[
            list[dict[str, Any]] | None,
            Field(description="Row filters, each {column, op, value}.", max_length=50),
        ] = None,
        page: Page = 1,
        limit: Limit = 50,
    ) -> Any:
        """Read a page of a version's rows."""
        return await gated_call(
            gate,
            FILES,
            lambda: client.get(
                f"/versions/{version_id}/rows",
                split=split,
                q=q,
                # The route takes the filter list as JSON text; sending a list keeps an agent's
                # text from being re-parsed by the tool layer (the MCP SDK decodes JSON-like strings).
                where=json.dumps(where, sort_keys=True) if where is not None else None,
                page=page,
                limit=limit,
            ),
        )

    @mcp.tool()
    async def dataworks_explain_row(
        version_id: VersionId,
        row_key: Annotated[
            str | None, Field(description="Row key (SHA-256).", max_length=64)
        ] = None,
        q: Annotated[
            str | None, Field(description="Find the row by text instead.", max_length=200)
        ] = None,
    ) -> Any:
        """A row's history through the build: which steps kept, changed or dropped it, and why."""
        return await gated_call(
            gate,
            FILES,
            lambda: client.get(f"/versions/{version_id}/rows/history", row_key=row_key, q=q),
        )

    @mcp.tool()
    async def dataworks_get_drop_log(version_id: VersionId) -> Any:
        """Rows each step dropped, counted by reason."""
        return await gated_call(gate, FILES, lambda: client.get(f"/versions/{version_id}/drop-log"))

    @mcp.tool()
    async def dataworks_list_row_events(
        version_id: VersionId,
        step_index: Annotated[int | None, Field(description="Filter by step.", ge=0)] = None,
        kind: Annotated[str | None, Field(description="Event kind.", max_length=16)] = None,
        reason_code: Annotated[str | None, Field(description="Reason code.", max_length=64)] = None,
        row_key: Annotated[str | None, Field(description="Row key.", max_length=64)] = None,
        page: Page = 1,
        limit: Limit = 50,
    ) -> Any:
        """List the version's row events (drops and changes) with filters."""
        return await gated_call(
            gate,
            READ,
            lambda: client.get(
                f"/versions/{version_id}/events",
                step_index=step_index,
                kind=kind,
                reason_code=reason_code,
                row_key=row_key,
                page=page,
                limit=limit,
            ),
        )

    @mcp.tool()
    async def dataworks_get_lineage(version_id: VersionId) -> Any:
        """The version's lineage: inputs back to sources, with recipe revisions."""
        return await gated_call(gate, READ, lambda: client.get(f"/versions/{version_id}/lineage"))

    @mcp.tool()
    async def dataworks_compare_versions(
        version_id: VersionId,
        with_version_id: Annotated[
            str, Field(description="The version to compare against.", pattern=ID_PATTERN)
        ],
    ) -> Any:
        """Compare two versions: rows added, removed and changed, and distribution shifts."""
        return await gated_call(
            gate,
            FILES,
            lambda: client.get(f"/versions/{version_id}/compare", **{"with": with_version_id}),
        )

    @mcp.tool()
    async def dataworks_verify_rebuild(version_id: VersionId) -> Any:
        """Rebuild the version from its recipe and inputs and check the row keys match. Starts a
        job."""
        return await gated_call(
            gate, JOB_FILES, lambda: client.post(f"/versions/{version_id}/verify-rebuild")
        )

    @mcp.tool()
    async def dataworks_delete_version(
        version_id: VersionId,
        reason: Annotated[
            str, Field(description="Why it is deleted; recorded.", min_length=1, max_length=2000)
        ],
    ) -> Any:
        """Delete a version (it becomes a tombstone). ALWAYS waits for the operator's approval
        when called by an agent (version_delete): returns {"approval_id", "status": "pending"}."""
        return await gated_call(
            gate,
            FILES,
            lambda: client.delete(f"/versions/{version_id}", json_body={"reason": reason}),
        )

    # ---------------------------------------------------------------- 002 recipes

    @mcp.tool()
    async def dataworks_list_recipes(
        q: Annotated[str | None, Field(description="Search the name.", max_length=100)] = None,
        archived: Annotated[bool, Field(description="List archived recipes instead.")] = False,
        page: Page = 1,
        limit: Limit = 50,
    ) -> Any:
        """List recipes."""
        return await gated_call(
            gate,
            READ,
            lambda: client.get("/recipes", q=q, archived=archived, page=page, limit=limit),
        )

    @mcp.tool()
    async def dataworks_save_recipe(
        name: Annotated[str, Field(description="Recipe name.", min_length=1, max_length=200)],
        body: Annotated[
            dict[str, Any], Field(description='The recipe body: {"steps": [...]} (002 FR-002.4).')
        ],
        description: Annotated[str | None, Field(description="Free text.", max_length=4000)] = None,
        step_labels: Annotated[
            list[str] | None, Field(description="A label per step, shown in the UI.")
        ] = None,
    ) -> Any:
        """Create a recipe with its first revision. Validate first with
        dataworks_validate_recipe."""
        payload = {
            "name": name,
            "description": description,
            "body": body,
            "step_labels": step_labels or [],
        }
        return await gated_call(gate, READ, lambda: client.post("/recipes", json_body=payload))

    @mcp.tool()
    async def dataworks_get_recipe(recipe_id: RecipeId) -> Any:
        """One recipe with its revisions and the canonical body of each."""
        return await gated_call(gate, READ, lambda: client.get(f"/recipes/{recipe_id}"))

    @mcp.tool()
    async def dataworks_revise_recipe(
        recipe_id: RecipeId,
        body: Annotated[dict[str, Any], Field(description="The new recipe body.")],
        step_labels: Annotated[list[str] | None, Field(description="A label per step.")] = None,
    ) -> Any:
        """Add a revision to a recipe. Earlier revisions and their versions are unchanged."""
        payload = {"body": body, "step_labels": step_labels or []}
        return await gated_call(
            gate, READ, lambda: client.post(f"/recipes/{recipe_id}/revisions", json_body=payload)
        )

    @mcp.tool()
    async def dataworks_clone_recipe(
        recipe_id: RecipeId,
        name: Annotated[str, Field(description="Name of the copy.", min_length=1, max_length=200)],
        revision_id: Annotated[
            str | None,
            Field(description="Revision to copy; default the latest.", pattern=ID_PATTERN),
        ] = None,
    ) -> Any:
        """Copy a recipe (one revision) into a new recipe."""
        payload = {"name": name, "revision_id": revision_id}
        return await gated_call(
            gate, READ, lambda: client.post(f"/recipes/{recipe_id}/clone", json_body=payload)
        )

    @mcp.tool()
    async def dataworks_validate_recipe(
        body: Annotated[dict[str, Any], Field(description="The recipe body to check.")],
    ) -> Any:
        """Check a recipe body without saving: operators exist and are allowed, parameters are
        valid, and the step order is legal."""
        return await gated_call(
            gate, READ, lambda: client.post("/recipes/validate", json_body={"body": body})
        )

    @mcp.tool()
    async def dataworks_archive_recipe(recipe_id: RecipeId) -> Any:
        """Archive a recipe: hidden from lists, still readable, its versions unchanged."""
        return await gated_call(gate, READ, lambda: client.post(f"/recipes/{recipe_id}/archive"))
