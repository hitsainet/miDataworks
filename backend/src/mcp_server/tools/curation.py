"""Category ``curation``: profile, shortcut audit, leakage, contamination, TRL validation and the
warning levels (feature 004, FR-004.45; 010 FTID section 3.7).

Every read and run route has one tool. The three level WRITES have none: warning levels are
read-only for agents (P-09), and the REST routes answer an agent ``403 agent_forbidden`` anyway;
they are exempt in the ledger with that reason. Run tools answer the stored report, an inline
result, or a job ID to follow with ``dataworks_job_status``.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from ..client import DataworksClient
from ..context import FILES, ID_PATTERN, JOB_FILES, READ, ToolContext
from ..health_gate import gated_call

VersionId = Annotated[str, Field(description="Version ID.", pattern=ID_PATTERN)]
DatasetId = Annotated[str, Field(description="Dataset ID.", pattern=ID_PATTERN)]
Column = Annotated[str, Field(description="A column name.", min_length=1, max_length=255)]
OptionalColumn = Annotated[
    str | None, Field(description="A column name, or omit.", min_length=1, max_length=255)
]
Page0 = Annotated[int, Field(ge=0, le=1_000_000, description="Page number, from 0.")]
Limit100 = Annotated[int, Field(ge=1, le=100, description="Rows per page, at most 100.")]


def register(mcp: MCPServer, client: DataworksClient, ctx: ToolContext) -> None:
    gate = ctx.gate

    @mcp.tool()
    async def dataworks_run_profile(
        version_id: VersionId,
        sample_size: Annotated[
            int | None, Field(gt=0, le=100_000, description="Profile a seeded sample of this size.")
        ] = None,
    ) -> Any:
        """Profile a version: counts, nulls, lengths, duplicates, clusters; figures that cannot be
        computed say why. Returns the stored or inline report, or a job ID."""
        return await gated_call(
            gate,
            JOB_FILES,
            lambda: client.post(
                f"/versions/{version_id}/profile", json_body={"sample_size": sample_size}
            ),
        )

    @mcp.tool()
    async def dataworks_get_profile(version_id: VersionId) -> Any:
        """The newest stored profile of a version (404 profile_not_run when none)."""
        return await gated_call(gate, READ, lambda: client.get(f"/versions/{version_id}/profile"))

    @mcp.tool()
    async def dataworks_run_shortcut_audit(
        version_id: VersionId, label_column: OptionalColumn = None
    ) -> Any:
        """Run the shortcut audit: does any metadata column predict the label on held-out folds,
        above chance and a permuted-label control? Omit label_column to use the labeler's."""
        return await gated_call(
            gate,
            JOB_FILES,
            lambda: client.post(
                f"/versions/{version_id}/shortcut-audit", json_body={"label_column": label_column}
            ),
        )

    @mcp.tool()
    async def dataworks_get_shortcut_audit(
        version_id: VersionId, label_column: OptionalColumn = None
    ) -> Any:
        """The stored audit with its warnings evaluated against the level in force now."""
        return await gated_call(
            gate,
            READ,
            lambda: client.get(f"/versions/{version_id}/shortcut-audit", label_column=label_column),
        )

    @mcp.tool()
    async def dataworks_get_shortcut_cells(
        version_id: VersionId,
        column: Column,
        value: Annotated[str, Field(description="The value, as the audit names it.")],
        label: Column,
        page: Page0 = 0,
        limit: Limit100 = 30,
    ) -> Any:
        """Seeded sample rows of one (value x label) cell of the audit."""
        return await gated_call(
            gate,
            FILES,
            lambda: client.get(
                f"/versions/{version_id}/shortcut-audit/cells",
                column=column,
                value=value,
                label=label,
                page=page,
                limit=limit,
            ),
        )

    @mcp.tool()
    async def dataworks_run_leakage_check(
        version_id: VersionId,
        group_column: OptionalColumn = None,
        threshold: Annotated[
            float | None, Field(gt=0, le=1, description="Near-duplicate Jaccard threshold.")
        ] = None,
    ) -> Any:
        """Find exact, near (MinHash) and group leakage across the version's splits."""
        body = {"group_column": group_column, "threshold": threshold}
        return await gated_call(
            gate, JOB_FILES, lambda: client.post(f"/versions/{version_id}/leakage", json_body=body)
        )

    @mcp.tool()
    async def dataworks_get_leakage_report(version_id: VersionId) -> Any:
        """The newest stored leakage report: crossing pairs counted per split pair."""
        return await gated_call(gate, READ, lambda: client.get(f"/versions/{version_id}/leakage"))

    @mcp.tool()
    async def dataworks_get_leakage_pairs(
        version_id: VersionId, page: Page0 = 0, limit: Limit100 = 50
    ) -> Any:
        """One page of the leakage report's crossing pairs, with excerpts."""
        return await gated_call(
            gate,
            FILES,
            lambda: client.get(f"/versions/{version_id}/leakage/pairs", page=page, limit=limit),
        )

    @mcp.tool()
    async def dataworks_run_contamination_check(
        version_id: VersionId,
        benchmark_source_ids: Annotated[
            list[str],
            Field(min_length=1, max_length=20, description="Benchmark source IDs (feature 001)."),
        ],
        n: Annotated[int | None, Field(ge=3, le=50, description="Word n-gram size.")] = None,
    ) -> Any:
        """Measure word n-gram overlap with benchmarks imported at pinned revisions."""
        body = {"benchmark_source_ids": benchmark_source_ids, "n": n}
        return await gated_call(
            gate,
            JOB_FILES,
            lambda: client.post(f"/versions/{version_id}/contamination", json_body=body),
        )

    @mcp.tool()
    async def dataworks_get_contamination(version_id: VersionId) -> Any:
        """The newest stored contamination report."""
        return await gated_call(
            gate, READ, lambda: client.get(f"/versions/{version_id}/contamination")
        )

    @mcp.tool()
    async def dataworks_validate_trl(
        version_id: VersionId,
        target_type: Annotated[str, Field(description="sft, dpo, kto, grpo_prompt or prm.")],
    ) -> Any:
        """Check a version against the TRL trainer's column contract; rules that could not be
        evaluated are listed as not checked, never as passed."""
        return await gated_call(
            gate,
            FILES,
            lambda: client.post(
                f"/versions/{version_id}/trl-validation", json_body={"target_type": target_type}
            ),
        )

    @mcp.tool()
    async def dataworks_get_warning_levels() -> Any:
        """The global shortcut warning level and its history (read-only for agents, P-09)."""
        return await gated_call(gate, READ, lambda: client.get("/settings/shortcut-level"))

    @mcp.tool()
    async def dataworks_get_dataset_warning_level(dataset_id: DatasetId) -> Any:
        """A dataset's effective warning level, its source and its override history."""
        return await gated_call(
            gate, READ, lambda: client.get(f"/datasets/{dataset_id}/shortcut-level")
        )

    @mcp.tool()
    async def dataworks_list_benchmarks() -> Any:
        """The benchmark catalogue (T-14): pinned revisions and whether each is imported."""
        return await gated_call(gate, READ, lambda: client.get("/curation/benchmarks"))
