"""Category ``code_rules``: operator code rules (feature 018; FR-018.27 - FR-018.36).

Seven tools, each ONE ``DataworksClient`` call (``test_mcp_tools_stay_on_http.py``): read the runner
contract, dry-run a document, save one (GATED: an agent's save of a hash the operator has not
approved waits for the operator, ``code_rule_save``), list, get, archive and unarchive. No tool
approves or revokes: that is the operator's alone, on the Code rules screen (FR-018.30), and the
routes refuse agents. Every result and refusal carries ``next``.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from ..client import DataworksClient
from ..context import (
    BEHAVIOUR_KEY_PATTERN,
    ArchiveReason,
    IncludeArchived,
    Limit,
    Page,
    ToolContext,
    archive_body,
)
from ..health_gate import gated_call

BACKEND_ONLY = ("backend",)
NAME = r"^[a-z0-9][a-z0-9-]{0,63}$"

CodeRuleName = Annotated[
    str,
    Field(
        description="A code rule's name: lower-case letters, digits and '-', at most 64.",
        pattern=NAME,
    ),
]
CodeRuleVersion = Annotated[int, Field(ge=1, description="A stored version number.")]
Document = Annotated[
    dict[str, Any],
    Field(
        description="A midataworks.runner/v1 code-rule document (docs/schemas/"
        "midataworks-runner-v1.json): runner_contract, source, functions, params_schema, "
        "derived_from. Its SHA-256 is what the operator approves."
    ),
]
Cases = Annotated[
    list[dict[str, Any]],
    Field(
        description="The rule's cases: {id, function, params, text, expect: {count} or "
        "{reason}, source}. Not hashed: editing only the cases needs no new approval."
    ),
]


def register(mcp: MCPServer, client: DataworksClient, ctx: ToolContext) -> None:
    gate = ctx.gate

    @mcp.tool()
    async def dataworks_get_runner_contract() -> Any:
        """The code-rule runner contract as this deployment serves it: language level, the
        modules a rule may import, the limits floor and the limits in force, the count cap, the
        reasons a call can end with, the diagnostic length, the dry-run rate and text limit, the
        caps, and whether code rules are switched on."""
        return await gated_call(gate, BACKEND_ONLY, lambda: client.get("/runner/contract"))

    @mcp.tool()
    async def dataworks_test_code_rule(
        document: Annotated[
            dict[str, Any] | None,
            Field(description="A code-rule document to try (omit it to name a stored one)."),
        ] = None,
        cases: Annotated[
            list[dict[str, Any]] | None,
            Field(description="Cases to run with an inline document."),
        ] = None,
        name: Annotated[
            str | None, Field(description="A stored code rule's name.", pattern=NAME)
        ] = None,
        version: Annotated[int | None, Field(ge=1, description="Its version.")] = None,
        texts: Annotated[
            list[str] | None,
            Field(description="Your own texts to run it on; never stored or logged."),
        ] = None,
        params: Annotated[
            dict[str, Any] | None,
            Field(description="Parameters for the texts (checked against params_schema)."),
        ] = None,
        functions: Annotated[
            list[str] | None, Field(description="Functions to run on the texts; default all.")
        ] = None,
    ) -> Any:
        """Dry-run a code rule in the isolated runner: its cases and your texts, per function a
        count or a reason, and - for a function that raised on your own text - the exception type,
        line and a short message. Unapproved code may be tried; nothing is stored. Rate-limited."""
        return await gated_call(
            gate,
            BACKEND_ONLY,
            lambda: client.post(
                "/code-rules/test",
                json_body={
                    "document": document,
                    "cases": cases,
                    "name": name,
                    "version": version,
                    "texts": texts or [],
                    "params": params or {},
                    "functions": functions,
                },
            ),
        )

    @mcp.tool()
    async def dataworks_save_code_rule(
        name: CodeRuleName,
        behaviour_key: Annotated[
            str,
            Field(
                description="The behaviour it serves (a name keeps one).",
                pattern=BEHAVIOUR_KEY_PATTERN,
            ),
        ],
        document: Document,
        cases: Cases,
    ) -> Any:
        """Save a code rule as version n+1 of its name. Every check runs first (the document, the
        caps, a firing and a non-firing case per function, the runner's load check, every case
        twice at the contract floor); a refusal names the item. A document whose hash the
        operator has not approved WAITS for the operator: you get a pending approval, and the
        operator reads the source and its case results before anything is stored."""
        return await gated_call(
            gate,
            BACKEND_ONLY,
            lambda: client.post(
                "/code-rules",
                json_body={
                    "name": name,
                    "behaviour_key": behaviour_key,
                    "document": document,
                    "cases": cases,
                },
            ),
        )

    @mcp.tool()
    async def dataworks_list_code_rules(
        page: Page = 1,
        limit: Limit = 50,
        behaviour_key: Annotated[
            str | None,
            Field(description="Only this behaviour's code rules.", pattern=BEHAVIOUR_KEY_PATTERN),
        ] = None,
        include_archived: IncludeArchived = False,
    ) -> Any:
        """List code rules, one row per name: latest version, hash, behaviour key, approval state,
        functions and last case result; archived ones only with include_archived."""
        return await gated_call(
            gate,
            BACKEND_ONLY,
            lambda: client.get(
                "/code-rules",
                page=page,
                limit=limit,
                behaviour_key=behaviour_key,
                include_archived=include_archived,
            ),
        )

    @mcp.tool()
    async def dataworks_get_code_rule(name: CodeRuleName, version: CodeRuleVersion) -> Any:
        """One code-rule version: the document, its hash, its cases, the approval state and
        history, the contract status (failing under a newer contract minor?) and superseded_by."""
        return await gated_call(
            gate, BACKEND_ONLY, lambda: client.get(f"/code-rules/{name}/versions/{version}")
        )

    @mcp.tool()
    async def dataworks_archive_code_rule(name: CodeRuleName, reason: ArchiveReason = None) -> Any:
        """Archive a code rule: hide it (every version) from lists. Nothing is deleted and its
        approval stands; dataworks_unarchive_code_rule restores it."""
        return await gated_call(
            gate,
            BACKEND_ONLY,
            lambda: client.post(f"/code-rules/{name}/archive", json_body=archive_body(reason)),
        )

    @mcp.tool()
    async def dataworks_unarchive_code_rule(
        name: CodeRuleName, reason: ArchiveReason = None
    ) -> Any:
        """Unarchive a code rule: list it again."""
        return await gated_call(
            gate,
            BACKEND_ONLY,
            lambda: client.post(f"/code-rules/{name}/unarchive", json_body=archive_body(reason)),
        )
