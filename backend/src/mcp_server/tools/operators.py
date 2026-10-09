"""Category ``operators``: the operator registry, read-only for agents (003 FR-003.24; P-09).

No tool here changes the allowlist: ``POST /operators/allowlist`` and ``.../revoke`` are exempt
(agents are read-only on it, P-09) and refused at REST for agent origin as well.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from ..client import DataworksClient
from ..context import FILES, ID_PATTERN, READ, Limit, Page, ToolContext
from ..health_gate import gated_call

OperatorName = Annotated[
    str, Field(description="Operator name, for example native.dedupe_exact.", pattern=ID_PATTERN)
]
OperatorVersion = Annotated[
    str, Field(description="Operator version, for example 1.0.0.", pattern=ID_PATTERN)
]
PreviewInput = Annotated[
    dict[str, Any],
    Field(description='{"version_id": ..., "split": ...} or {"step_execution_id": ...}.'),
]


def register(mcp: MCPServer, client: DataworksClient, ctx: ToolContext) -> None:
    gate = ctx.gate

    @mcp.tool()
    async def dataworks_list_operators(
        provider: Annotated[
            str | None,
            Field(description="Filter: native, data_designer, datajuicer, ...", max_length=128),
        ] = None,
        kind: Annotated[
            str | None, Field(description="Filter by operator kind.", max_length=32)
        ] = None,
        state: Annotated[str | None, Field(description="Filter by state.", max_length=32)] = None,
        page: Page = 1,
        limit: Limit = 50,
    ) -> Any:
        """List operators (the steps a recipe can use) with provider, kind and state."""
        return await gated_call(
            gate,
            READ,
            lambda: client.get(
                "/operators", provider=provider, kind=kind, state=state, page=page, limit=limit
            ),
        )

    @mcp.tool()
    async def dataworks_list_operator_versions(name: OperatorName) -> Any:
        """Every version of one operator."""
        return await gated_call(gate, READ, lambda: client.get(f"/operators/{name}"))

    @mcp.tool()
    async def dataworks_get_operator(name: OperatorName, version: OperatorVersion) -> Any:
        """One operator version's manifest, including its parameter schema."""
        return await gated_call(gate, READ, lambda: client.get(f"/operators/{name}/{version}"))

    @mcp.tool()
    async def dataworks_validate_operator_params(
        name: OperatorName,
        version: OperatorVersion,
        params: Annotated[dict[str, Any], Field(description="Parameters to check.")],
    ) -> Any:
        """Check parameters against the operator's schema without running it."""
        return await gated_call(
            gate,
            READ,
            lambda: client.post(
                f"/operators/{name}/{version}/validate", json_body={"params": params}
            ),
        )

    @mcp.tool()
    async def dataworks_preview_operator(
        name: OperatorName,
        version: OperatorVersion,
        input: PreviewInput,
        params: Annotated[dict[str, Any] | None, Field(description="Operator parameters.")] = None,
        sample_size: Annotated[int | None, Field(description="Rows to sample.", ge=1)] = None,
        seed: Annotated[int, Field(description="Sampling seed.", ge=0)] = 0,
    ) -> Any:
        """Run an operator on a sample and show kept, changed and dropped rows. Returns a preview
        ID; read it with dataworks_get_operator_preview."""
        body = {"params": params or {}, "input": input, "sample_size": sample_size, "seed": seed}
        return await gated_call(
            gate,
            FILES,
            lambda: client.post(f"/operators/{name}/{version}/preview", json_body=body),
        )

    @mcp.tool()
    async def dataworks_get_operator_preview(
        preview_id: Annotated[str, Field(description="Preview ID.", pattern=ID_PATTERN)],
    ) -> Any:
        """A preview's state and result."""
        return await gated_call(gate, READ, lambda: client.get(f"/operators/previews/{preview_id}"))

    @mcp.tool()
    async def dataworks_preview_threshold(
        name: OperatorName,
        version: OperatorVersion,
        input: PreviewInput,
        params: Annotated[dict[str, Any] | None, Field(description="Operator parameters.")] = None,
        sample_size: Annotated[int | None, Field(description="Rows to sample.", ge=1)] = None,
        seed: Annotated[int, Field(description="Sampling seed.", ge=0)] = 0,
    ) -> Any:
        """The distribution of an operator's score on a sample, to choose a threshold."""
        body = {"params": params or {}, "input": input, "sample_size": sample_size, "seed": seed}
        return await gated_call(
            gate,
            FILES,
            lambda: client.post(f"/operators/{name}/{version}/statistics", json_body=body),
        )

    @mcp.tool()
    async def dataworks_get_allowlist() -> Any:
        """The operator allowlist (installed plug-in operators allowed to run). Read-only for
        agents: only the operator can change it (P-09)."""
        return await gated_call(gate, READ, lambda: client.get("/operators/allowlist"))

    @mcp.tool()
    async def dataworks_plan_recipe_upgrade(
        recipe_body: Annotated[
            dict[str, Any],
            Field(description='A recipe body, {"steps": [...]}, to plan upgrades for.'),
        ],
    ) -> Any:
        """Which steps of a recipe have newer operator versions, and what changes."""
        return await gated_call(
            gate,
            READ,
            lambda: client.post("/operators/upgrade-plan", json_body={"recipe_body": recipe_body}),
        )
