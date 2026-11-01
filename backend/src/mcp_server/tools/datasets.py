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
    BEHAVIOUR_KEY_PATTERN,
    FILES,
    ID_PATTERN,
    JOB,
    JOB_FILES,
    READ,
    REPO_ID_PATTERN,
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

#: The largest file the MCP TRANSPORT carries, decoded. It is not the upload policy: that is the
#: operator's ``upload_max_bytes`` (Settings → Environment), which the backend serves at
#: ``GET /sources/meta`` and enforces on every upload, and which ``dataworks_upload_file`` reads
#: per call (``upload_limit``). This ceiling exists because the file travels base64-encoded inside
#: one JSON-RPC request: the server's body limit (server.MAX_REQUEST_BODY_BYTES) is derived from
#: it and the MCP Ingress admits 16m (k8s/base/mcp.yaml; tests/unit/test_ingress_body_limit.py).
#: Larger files are uploaded in the UI (FTID IQ7).
TRANSPORT_MAX_BYTES = 10 * 1024 * 1024

TargetType = Literal["sft", "dpo", "kto", "grpo_prompt", "prm", "detector", "untyped"]
SourceId = Annotated[str, Field(description="Source ID, src_...", pattern=ID_PATTERN)]
DatasetId = Annotated[str, Field(description="Dataset ID, ds_...", pattern=ID_PATTERN)]
VersionId = Annotated[str, Field(description="Version ID, ver_...", pattern=ID_PATTERN)]
RecipeId = Annotated[str, Field(description="Recipe ID, rcp_...", pattern=ID_PATTERN)]
#: 017 FR-017.58: the large result tables and what shapes them.
ResultKind = Annotated[
    Literal["census", "proof", "agreement"],
    Field(description="census or proof (of a version) or agreement (of a label run, by stratum)."),
]
ResultObject = Annotated[
    str, Field(description="The version ID (census, proof) or label run ID.", pattern=ID_PATTERN)
]
AgreementColumn = Annotated[
    str | None, Field(description="agreement: the label column compared.", max_length=256)
]
AgreementPositive = Annotated[
    str | None, Field(description="agreement: the column's positive value.", max_length=256)
]
AgreementBy = Annotated[
    list[str] | None, Field(description="agreement: the strata columns.", max_length=8)
]


def compact(body: dict[str, Any]) -> dict[str, Any]:
    """The body without unset fields, so the backend's own defaults apply."""
    return {k: v for k, v in body.items() if v is not None}


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


def _transport_refusal() -> ToolError:
    return ToolError(
        f"The file is over {TRANSPORT_MAX_BYTES:,} bytes (10 MiB), the most one MCP request "
        "carries; upload it in the UI."
    )


def decode_upload(content_base64: str) -> bytes:
    """The decoded file, refused over the transport ceiling or when not base64, before any
    request. The operator's limit is checked next, by ``check_upload_limit``.

    Refusals are ``ToolError``: mcp 2.x shows the agent a tool's message only for that type.
    """
    if len(content_base64) > (TRANSPORT_MAX_BYTES * 4) // 3 + 8:
        raise _transport_refusal()
    try:
        raw = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ToolError("content_base64 is not valid base64.") from exc
    if len(raw) > TRANSPORT_MAX_BYTES:
        raise _transport_refusal()
    return raw


def upload_limit(meta: Any) -> int | None:
    """The operator's upload limit in force, from ``GET /sources/meta`` (``limits``), or None.

    One source: the value the backend enforces (``upload_max_bytes``: the Settings value, else
    the ``UPLOAD_MAX_BYTES`` default). None when the backend served no usable number; the backend
    still refuses an over-limit upload itself, so None loses only the early refusal.
    """
    limits = meta.get("limits") if isinstance(meta, dict) else None
    value = limits.get("upload_max_bytes") if isinstance(limits, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def check_upload_limit(raw: bytes, limit: int | None) -> None:
    """Refuse a file over the operator's limit, with the backend's code and next step."""
    if limit is not None and len(raw) > limit:
        raise ToolError(
            f"upload_too_large: the file is {len(raw):,} bytes, over the operator's "
            f"{limit:,}-byte upload limit. Split it, or ask the operator to raise the limit in "
            "Settings → Environment."
        )


def register(mcp: MCPServer, client: DataworksClient, ctx: ToolContext) -> None:
    gate = ctx.gate

    # ---------------------------------------------------------------- 001 sources

    @mcp.tool()
    async def dataworks_search_hf_datasets(
        query: Annotated[
            str, Field(description="Words to search for.", min_length=1, max_length=200)
        ],
        licences: Annotated[
            list[Annotated[str, Field(min_length=1, max_length=64)]] | None,
            Field(
                description="Hub licence ids to accept (any of them), for example mit, "
                "apache-2.0, cc-by-4.0.",
                max_length=5,
            ),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=100, description="Results, at most 100.")] = 20,
    ) -> Any:
        """Search the Hugging Face Hub for datasets: repository, licence, size category,
        downloads, gating and tasks for each, most downloaded first. Uses the stored HF token
        (never shown). Next: dataworks_preview_hf_dataset on a candidate."""
        body = {"query": query, "licences": licences or [], "limit": limit}
        return await gated_call(
            gate, JOB, lambda: client.post("/sources/hf/search", json_body=body)
        )

    @mcp.tool()
    async def dataworks_profile_source(
        source_id: SourceId,
        split: Annotated[
            str | None, Field(description="One split; all when omitted.", max_length=128)
        ] = None,
        value_columns: Annotated[
            list[Annotated[str, Field(min_length=1, max_length=255)]] | None,
            Field(
                description="Columns whose value shares to count (a label, a flag).", max_length=10
            ),
        ] = None,
        text_columns: Annotated[
            list[Annotated[str, Field(min_length=1, max_length=255)]] | None,
            Field(
                description="Columns whose lengths to describe; the detected text column when "
                "omitted.",
                max_length=10,
            ),
        ] = None,
        top: Annotated[int, Field(ge=1, le=200, description="Values shown per column.")] = 20,
    ) -> Any:
        """Whole-split statistics of an imported source before building a version: rows, nulls
        per column, value shares of the columns named, text length distribution. A split over
        the operator's cap (SOURCE_PROFILE_MAX_ROWS) is refused with the cap named."""
        body: dict[str, Any] = {"split": split, "value_columns": value_columns or [], "top": top}
        if text_columns is not None:
            body["text_columns"] = text_columns
        return await gated_call(
            gate, FILES, lambda: client.post(f"/sources/{source_id}/profile", json_body=body)
        )

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
        max_rows: Annotated[
            int | None,
            Field(
                description="Keep only the first N rows of each split (a slice is its own source).",
                ge=1,
                le=10_000_000,
            ),
        ] = None,
        data_files: Annotated[
            list[str] | None,
            Field(
                description="Read only these data files of the repository (repo-relative paths or "
                "globs, at the pinned commit).",
                min_length=1,
                max_length=64,
            ),
        ] = None,
    ) -> Any:
        """Import an HF dataset as a source. Starts a job (poll dataworks_job_status). With an
        access_token the call waits for the operator's approval. max_rows and data_files import a
        slice: the first rows of the named files, recorded on the source as import_slice."""
        body: dict[str, Any] = {
            "repo_id": repo_id,
            "config": config,
            "split": split,
            "revision": revision,
            "access_token": access_token,
            "confirm_large": confirm_large,
        }
        if max_rows is not None:
            body["max_rows"] = max_rows
        if data_files is not None:
            body["data_files"] = data_files
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
            str,
            Field(
                description=(
                    "The file's bytes, base64-encoded. At most the operator's upload limit "
                    "(dataworks_get_source_meta, limits.upload_max_bytes), and never over 10 MiB "
                    "decoded, the most one MCP request carries."
                )
            ),
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
        """Upload one Parquet, JSONL or CSV file (at most the operator's upload limit, and 10 MiB
        over MCP) as a source. Larger files are uploaded in the UI. Starts a job."""
        raw = decode_upload(content_base64)
        manifest: dict[str, Any] = {"files": [{"name": filename, "split": split}]}
        if csv is not None:
            manifest["csv"] = csv
        if display_name is not None:
            manifest["display_name"] = display_name

        async def upload() -> Any:
            # The operator's limit, read per call: a change in Settings applies to the next
            # upload, as in the UI. Refused here, before the file is sent.
            check_upload_limit(raw, upload_limit(await client.get("/sources/meta")))
            return await client.post_multipart(
                "/sources/uploads",
                files=[("files", (filename, raw, "application/octet-stream"))],
                data={"manifest": json.dumps(manifest, sort_keys=True)},
            )

        return await gated_call(gate, JOB_FILES, upload)

    @mcp.tool()
    async def dataworks_create_index_source(
        rows: Annotated[
            int, Field(description="How many rows (1 to 10,000,000).", ge=1, le=10_000_000)
        ],
        column: Annotated[
            str,
            Field(
                description="The integer column's name.",
                pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$",
            ),
        ] = "idx",
        start: Annotated[int, Field(description="The first value.", ge=0)] = 0,
        split: Annotated[
            str, Field(description="Split name.", min_length=1, max_length=64)
        ] = "train",
        display_name: Annotated[
            str | None, Field(description="Name shown for the source.", max_length=200)
        ] = None,
    ) -> Any:
        """Make a source of N rows from nothing - one integer column, start..start+rows-1 - to
        drive a generator such as synth_identifier; no file upload. The same request returns the
        same source. Build from it with column_roles {"<column>": "content"}. Starts an import
        job (or returns the existing source)."""
        body: dict[str, Any] = {"rows": rows, "column": column, "start": start, "split": split}
        if display_name is not None:
            body["display_name"] = display_name
        return await gated_call(
            gate, JOB_FILES, lambda: client.post("/sources/index", json_body=body)
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
        include_archived: IncludeArchived = False,
        page: Page = 1,
        limit: Limit = 50,
    ) -> Any:
        """List datasets with their latest version. Archived datasets are left out unless
        include_archived is true."""
        return await gated_call(
            gate,
            READ,
            lambda: client.get(
                "/datasets",
                q=q,
                target_type=target_type,
                include_archived=include_archived,
                page=page,
                limit=limit,
            ),
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

    @mcp.tool()
    async def dataworks_archive_dataset(dataset_id: DatasetId, reason: ArchiveReason = None) -> Any:
        """Archive a dataset: hide it and its versions from default lists and pickers. Nothing
        is deleted; every version, publish and lineage record still reads, and
        dataworks_unarchive_dataset restores it. Refused (dataset_busy) while a job works on it."""
        return await gated_call(
            gate,
            READ,
            lambda: client.post(f"/datasets/{dataset_id}/archive", json_body=archive_body(reason)),
        )

    @mcp.tool()
    async def dataworks_unarchive_dataset(
        dataset_id: DatasetId, reason: ArchiveReason = None
    ) -> Any:
        """Unarchive a dataset: list it again."""
        return await gated_call(
            gate,
            READ,
            lambda: client.post(
                f"/datasets/{dataset_id}/unarchive", json_body=archive_body(reason)
            ),
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
    async def dataworks_plan_build(
        dataset_id: DatasetId,
        recipe_revision_id: Annotated[
            str, Field(description="Recipe revision to plan, rrv_...", pattern=ID_PATTERN)
        ],
        inputs: Annotated[
            list[dict[str, Any]],
            Field(description="The inputs the build would take.", min_length=1, max_length=16),
        ],
        seed: Annotated[int | None, Field(description="Seed the build would use.", ge=0)] = None,
        column_roles: Annotated[
            dict[str, Literal["content", "metadata"]] | None,
            Field(description="Per-column role overrides the build would take."),
        ] = None,
    ) -> Any:
        """Dry run of dataworks_build_version; writes nothing. Says whether the recipe is valid
        for these inputs, which steps count against the 5,000-row agent labelling window and
        why (only model-calling steps and probe/feature labelers count), the rows counted, the
        window total now and whether you would wait for approval, and for each cell_balancer
        the cells and the cap (the SINGLE smallest cell of the whole table, a global minimum)."""
        body = {
            "dataset_id": dataset_id,
            "recipe_revision_id": recipe_revision_id,
            "inputs": inputs,
            "seed": seed,
            "bindings": [],
            "column_roles": column_roles or {},
        }
        return await gated_call(gate, FILES, lambda: client.post("/versions/plan", json_body=body))

    @mcp.tool()
    async def dataworks_list_versions(
        dataset_id: Annotated[
            str | None, Field(description="Filter by dataset ID.", pattern=ID_PATTERN)
        ] = None,
        state: Annotated[
            Literal["completed", "deleted"] | None, Field(description="Filter by state.")
        ] = None,
        order: Annotated[
            Literal["name", "recent"],
            Field(description="name: by dataset name; recent: newest version first."),
        ] = "name",
        include_archived: IncludeArchived = False,
        page: Page = 1,
        limit: Limit = 50,
    ) -> Any:
        """List versions. Without a dataset_id, the versions of archived datasets are left out
        unless include_archived is true."""
        return await gated_call(
            gate,
            READ,
            lambda: client.get(
                "/versions",
                dataset_id=dataset_id,
                state=state,
                order=order,
                include_archived=include_archived,
                page=page,
                limit=limit,
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
        columns: Annotated[
            list[Annotated[str, Field(min_length=1, max_length=255)]] | None,
            Field(
                description="Return only these columns (plus _dw_row_key); all when omitted.",
                max_length=200,
            ),
        ] = None,
        page: Page = 1,
        limit: Limit = 50,
    ) -> Any:
        """Read a page of a version's rows, optionally only the named columns. For a whole
        split use dataworks_get_split_download_url instead of paging."""
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
                columns=",".join(columns) if columns else None,
                page=page,
                limit=limit,
            ),
        )

    @mcp.tool()
    async def dataworks_get_split_download_url(
        version_id: VersionId,
        split: Annotated[
            str,
            Field(description="The split, for example train.", max_length=64, pattern=ID_PATTERN),
        ],
        columns: Annotated[
            list[Annotated[str, Field(min_length=1, max_length=255)]] | None,
            Field(description="Only these columns (plus _dw_row_key).", max_length=200),
        ] = None,
        format: Annotated[
            Literal["parquet", "jsonl"], Field(description="parquet or jsonl (JSON Lines).")
        ] = "parquet",
        expires_in_s: Annotated[
            int, Field(ge=60, le=86_400, description="Seconds the link works, 60 to 86400.")
        ] = 3600,
    ) -> Any:
        """A signed, expiring URL that downloads one whole split (optionally only some columns)
        with a plain HTTP GET, no token needed. The bytes do not pass through MCP. Without
        PUBLIC_API_URL set the answer is a path: prefix the miDataworks address you reach."""
        body: dict[str, Any] = {"format": format, "expires_in_s": expires_in_s}
        if columns:
            body["columns"] = columns
        return await gated_call(
            gate,
            FILES,
            lambda: client.post(
                f"/versions/{version_id}/splits/{split}/download-url", json_body=body
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
        behaviour_key: BehaviourFilter = None,
    ) -> Any:
        """List recipes."""
        return await gated_call(
            gate,
            READ,
            lambda: client.get(
                "/recipes",
                q=q,
                archived=archived,
                page=page,
                limit=limit,
                behaviour_key=behaviour_key,
            ),
        )

    @mcp.tool()
    async def dataworks_save_recipe(
        name: Annotated[str, Field(description="Recipe name.", min_length=1, max_length=200)],
        body: Annotated[
            dict[str, Any] | None,
            Field(
                description='The recipe body: {"steps": [...]} (002 FR-002.4), at most 64 steps. '
                'A step may be {"include": "<revision id>", "from": i, "to": j} to reuse a saved '
                "revision's steps i..j. Omit when recipe_revision_id is given."
            ),
        ] = None,
        recipe_revision_id: Annotated[
            str | None,
            Field(description="Start from a saved revision's body instead.", pattern=ID_PATTERN),
        ] = None,
        description: Annotated[str | None, Field(description="Free text.", max_length=4000)] = None,
        step_labels: Annotated[
            list[str] | None, Field(description="A label per step, shown in the UI.")
        ] = None,
        echo: Annotated[
            bool, Field(description="Return the whole recipe with its body (default: a summary).")
        ] = False,
        behaviour_key: BehaviourKeyArg = None,
    ) -> Any:
        """Create a recipe with its first revision. Answers ids, revision, recipe hash and a step
        census, not the body (echo=true for the full recipe). Validate first with
        dataworks_validate_recipe. behaviour_key is recorded on the recipe, not in its hash."""
        payload: dict[str, Any] = {
            "name": name,
            "description": description,
            "body": body,
            "step_labels": step_labels or [],
        }
        if recipe_revision_id is not None:
            payload["recipe_revision_id"] = recipe_revision_id
        if behaviour_key is not None:
            payload["behaviour_key"] = behaviour_key
        view = "full" if echo else "summary"
        return await gated_call(
            gate, READ, lambda: client.post("/recipes", json_body=payload, view=view)
        )

    @mcp.tool()
    async def dataworks_resolve_behaviour(
        behaviour_key: Annotated[
            str,
            Field(
                description="The behaviour key (slug shared with miStudio's catalogue).",
                pattern=BEHAVIOUR_KEY_PATTERN,
            ),
        ],
    ) -> Any:
        """Read a behaviour's definition from miStudio's catalogue now (017): verified with its
        version, positive and negative meaning and target FPR; or "not in the catalogue"; or "not
        verified against a catalogue" when miStudio is absent or did not answer (then supply the
        definition yourself). Pass the answer's record as carrier_plan@1's definition so the
        build records what it was built against. miDataworks keeps no behaviour registry."""
        return await gated_call(
            gate, READ, lambda: client.get(f"/behaviours/{behaviour_key}/resolve")
        )

    @mcp.tool()
    async def dataworks_get_behaviour_status(
        behaviour_key: Annotated[
            str,
            Field(
                description="The behaviour key (slug shared with miStudio's catalogue).",
                pattern=BEHAVIOUR_KEY_PATTERN,
            ),
        ],
    ) -> Any:
        """Where a behaviour's dataset stands (017): its objects found by their behaviour_key
        (rule sets, templates, recipes, runs, versions, label runs, review queues, sets, sends)
        with what each last said; the ordered journey, each step with the tool to call; the next
        missing step; and every approval the remaining steps may need. Reads only; stores
        nothing. Call it first, and again after each step."""
        return await gated_call(
            gate, READ, lambda: client.get(f"/behaviours/{behaviour_key}/status")
        )

    @mcp.tool()
    async def dataworks_get_clone_difference(
        kind: Annotated[
            Literal["recipe", "generation_template"], Field(description="What was cloned.")
        ],
        a: Annotated[str, Field(description="The source's ID.", pattern=ID_PATTERN)],
        b: Annotated[str, Field(description="The clone's ID.", pattern=ID_PATTERN)],
    ) -> Any:
        """What a clone changed (017): every difference from a to b over the behaviour key and
        the body ({op, path, old, new}; JSON Pointers), so a clone made with substitutions reads
        back exactly those fields. Rule sets: dataworks_get_rule_set_difference."""
        return await gated_call(
            gate,
            READ,
            lambda: client.post("/differences", json_body={"kind": kind, "a": a, "b": b}),
        )

    @mcp.tool()
    async def dataworks_get_result_page(
        kind: ResultKind,
        id: ResultObject,  # noqa: A002 - the route's name for it
        column: AgreementColumn = None,
        positive_value: AgreementPositive = None,
        by: AgreementBy = None,
        limit: Annotated[int, Field(ge=1, le=500, description="Rows per page.")] = 50,
        page_token: Annotated[
            str | None, Field(description="next_page_token of the last page.", max_length=64)
        ] = None,
    ) -> Any:
        """One page of a large result table, worst first (017): a version's carrier census
        (kind census), a version's held-out proof values (proof), or a label run's agreement by
        stratum (agreement; shaped by column, positive_value and by). Carries rows_total and
        next_page_token; dataworks_get_result_download_url gives the whole table as a file."""
        return await gated_call(
            gate,
            READ,
            lambda: client.get(
                f"/results/{kind}/{id}",
                column=column,
                positive_value=positive_value,
                by=by,
                limit=limit,
                page_token=page_token,
            ),
        )

    @mcp.tool()
    async def dataworks_get_result_download_url(
        kind: ResultKind,
        id: ResultObject,  # noqa: A002 - the route's name for it
        column: AgreementColumn = None,
        positive_value: AgreementPositive = None,
        by: AgreementBy = None,
        expires_in_s: Annotated[
            int, Field(ge=60, le=86_400, description="Seconds the link stays valid.")
        ] = 3600,
    ) -> Any:
        """A signed, expiring link to a whole result table as JSON Lines (017): the rows every
        page of dataworks_get_result_page would give, in the same order. Fetch the URL directly;
        the bytes do not pass through MCP."""
        payload = compact(
            {
                "column": column,
                "positive_value": positive_value,
                "by": by,
                "expires_in_s": expires_in_s,
            }
        )
        return await gated_call(
            gate,
            READ,
            lambda: client.post(f"/results/{kind}/{id}/download-url", json_body=payload),
        )

    @mcp.tool()
    async def dataworks_get_recipe(recipe_id: RecipeId) -> Any:
        """One recipe with its revisions and the canonical body of each."""
        return await gated_call(gate, READ, lambda: client.get(f"/recipes/{recipe_id}"))

    @mcp.tool()
    async def dataworks_revise_recipe(
        recipe_id: RecipeId,
        body: Annotated[
            dict[str, Any] | None,
            Field(description="The new recipe body (steps may be include blocks)."),
        ] = None,
        recipe_revision_id: Annotated[
            str | None,
            Field(description="Use a saved revision's body instead.", pattern=ID_PATTERN),
        ] = None,
        step_labels: Annotated[list[str] | None, Field(description="A label per step.")] = None,
        echo: Annotated[
            bool, Field(description="Return the revision with its body (default: a summary).")
        ] = False,
    ) -> Any:
        """Add a revision to a recipe. Earlier revisions and their versions are unchanged.
        Answers a summary with the recipe hash (echo=true for the body)."""
        payload: dict[str, Any] = {"body": body, "step_labels": step_labels or []}
        if recipe_revision_id is not None:
            payload["recipe_revision_id"] = recipe_revision_id
        view = "full" if echo else "summary"
        return await gated_call(
            gate,
            READ,
            lambda: client.post(f"/recipes/{recipe_id}/revisions", json_body=payload, view=view),
        )

    @mcp.tool()
    async def dataworks_clone_recipe(
        recipe_id: RecipeId,
        name: Annotated[str, Field(description="Name of the copy.", min_length=1, max_length=200)],
        revision_id: Annotated[
            str | None,
            Field(description="Revision to copy; default the latest.", pattern=ID_PATTERN),
        ] = None,
        behaviour_key: BehaviourKeyArg = None,
        substitutions: Substitutions = None,
    ) -> Any:
        """Copy a recipe (one revision) into a new recipe. The copy keeps the source's
        behaviour_key unless one is given. substitutions change the copied body (JSON Pointers,
        in order); the result is validated like a saved body. Read what changed with
        dataworks_get_clone_difference."""
        payload: dict[str, Any] = {"name": name, "revision_id": revision_id}
        if behaviour_key is not None:
            payload["behaviour_key"] = behaviour_key
        if substitutions:
            payload["substitutions"] = substitutions
        return await gated_call(
            gate, READ, lambda: client.post(f"/recipes/{recipe_id}/clone", json_body=payload)
        )

    @mcp.tool()
    async def dataworks_validate_recipe(
        body: Annotated[
            dict[str, Any] | None,
            Field(description="The recipe body to check (or recipe_revision_id)."),
        ] = None,
        recipe_revision_id: Annotated[
            str | None, Field(description="Check a saved revision instead.", pattern=ID_PATTERN)
        ] = None,
        columns: Annotated[
            list[str] | None,
            Field(
                description="The input's columns: each step's column references and every Data "
                "Designer template are then checked against what that step will see.",
                max_length=5000,
            ),
        ] = None,
    ) -> Any:
        """Check a recipe without saving: operators exist and are allowed, parameters are valid,
        the step order is legal and it has at most 64 steps (limits states every limit). Each Data
        Designer (dd_*) step's templates are checked by the designer worker against Data
        Designer's own rules (template_invalid names the rule: too_deep, too_many_nodes,
        unknown_reference ...; designer_unavailable when the worker could not check them).
        dataworks_plan_build does the same against a build's real inputs."""
        payload: dict[str, Any] = {"body": body}
        if recipe_revision_id is not None:
            payload["recipe_revision_id"] = recipe_revision_id
        if columns is not None:
            payload["columns"] = columns
        return await gated_call(
            gate, READ, lambda: client.post("/recipes/validate", json_body=payload)
        )

    @mcp.tool()
    async def dataworks_archive_recipe(recipe_id: RecipeId) -> Any:
        """Archive a recipe: hidden from lists, still readable, its versions unchanged."""
        return await gated_call(gate, READ, lambda: client.post(f"/recipes/{recipe_id}/archive"))
