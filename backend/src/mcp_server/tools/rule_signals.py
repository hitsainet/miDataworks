"""Category ``rule_signals``: the rule-signal library (feature 016; FR-016.5, FR-016.35).

The validator list and get (slice 1); rule-set save, validate, preview, list, get, clone,
difference, archive and unarchive (slice 2); the derivation tools join in slice 4. Every tool is one
``DataworksClient`` call (``test_mcp_tools_stay_on_http.py``); none is gated (save writes data but
loads no model and calls nothing external, ADR-013). Every result and refusal of the rule-set
routes carries ``next``: the tool to call after it.
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

#: The validator library is code in the backend: only the backend must answer.
BACKEND_ONLY = ("backend",)

ValidatorId = Annotated[
    str,
    Field(
        description="A validator id: a format (card_pan, iban, ...) or an algorithm (luhn, ...).",
        pattern=r"^[a-z][a-z0-9_]{0,47}$",
    ),
]
ValidatorVersion = Annotated[
    int, Field(ge=1, description="The exact validator version (an integer).")
]
RuleSetName = Annotated[
    str,
    Field(
        description="A rule set's name: lower-case letters, digits and '-', at most 64.",
        pattern=r"^[a-z0-9][a-z0-9-]{0,63}$",
    ),
]
OptionalRuleSetName = Annotated[
    str | None,
    Field(
        description="A stored rule set's name (omit it to give a document instead).",
        pattern=r"^[a-z0-9][a-z0-9-]{0,63}$",
    ),
]
RuleSetVersion = Annotated[
    int | None, Field(ge=1, description="A stored version number; omitted = the latest.")
]
Document = Annotated[
    dict[str, Any] | None,
    Field(
        description="A midataworks.rule-set/v1 document (docs/schemas/midataworks-rule-set-v1.json)"
        "; omit it to name a stored rule set instead."
    ),
]
BehaviourKey = Annotated[
    str, Field(description="A behaviour key (the suite slug).", pattern=BEHAVIOUR_KEY_PATTERN)
]
RuleSetRef = Annotated[str, Field(description="<name>@<version>, e.g. my-rules@2.", max_length=100)]


def register(mcp: MCPServer, client: DataworksClient, ctx: ToolContext) -> None:
    gate = ctx.gate

    @mcp.tool()
    async def dataworks_list_validators(page: Page = 1, limit: Limit = 50) -> Any:
        """List the validator library: every registered validator version with its reasons,
        outputs, parameters, superseded_by and how many conformance cases it has."""
        return await gated_call(
            gate, BACKEND_ONLY, lambda: client.get("/validators", page=page, limit=limit)
        )

    @mcp.tool()
    async def dataworks_get_validator(validator_id: ValidatorId, version: ValidatorVersion) -> Any:
        """One validator version: its declaration and its cases from rule-signal-cases-v1.json."""
        return await gated_call(
            gate,
            BACKEND_ONLY,
            lambda: client.get(f"/validators/{validator_id}/versions/{version}"),
        )

    # --- rule sets (slice 2) -------------------------------------------------------------------

    @mcp.tool()
    async def dataworks_save_rule_set(
        document: Annotated[
            dict[str, Any],
            Field(
                description="A midataworks.rule-set/v1 document "
                "(docs/schemas/midataworks-rule-set-v1.json)."
            ),
        ],
    ) -> Any:
        """Save a rule set. Every stage must pass or nothing is stored: the schema, Unicode NFC,
        the caps, the pattern dialect, known validators, no code_rule yet, the minimum cases (a
        firing and a non-firing case per output, a case per veto) and every embedded case. Saving
        an edit makes version n+1 of the name; a name keeps one behaviour key. Each refusal names
        the item and next."""
        return await gated_call(
            gate, BACKEND_ONLY, lambda: client.post("/rule-sets", json_body={"document": document})
        )

    @mcp.tool()
    async def dataworks_validate_rule_set(
        document: Document = None, name: OptionalRuleSetName = None, version: RuleSetVersion = None
    ) -> Any:
        """Check a rule set without saving: every problem and every failing case, each with the
        expected and actual outputs, every signal's verdict and the step that decided. Give a
        document inline, or a stored name (and version)."""
        return await gated_call(
            gate,
            BACKEND_ONLY,
            lambda: client.post(
                "/rule-sets/validate",
                json_body={"document": document, "name": name, "version": version},
            ),
        )

    @mcp.tool()
    async def dataworks_preview_rule_set(
        text: Annotated[str, Field(description="The text to evaluate; never stored or logged.")],
        document: Document = None,
        name: OptionalRuleSetName = None,
        version: RuleSetVersion = None,
        element: Annotated[
            str | None, Field(description="The element inside the text, if the rule set reads one.")
        ] = None,
    ) -> Any:
        """Run a rule set on one text: per signal whether it fired, its spans (in characters,
        i.e. code points), substrings and outputs; the final outputs, the veto verdict and the
        step that decided each output. Nothing is stored."""
        return await gated_call(
            gate,
            BACKEND_ONLY,
            lambda: client.post(
                "/rule-sets/preview",
                json_body={
                    "text": text,
                    "document": document,
                    "name": name,
                    "version": version,
                    "element": element,
                },
            ),
        )

    @mcp.tool()
    async def dataworks_list_rule_sets(
        page: Page = 1,
        limit: Limit = 50,
        behaviour_key: Annotated[
            str | None,
            Field(description="Only this behaviour's rule sets.", pattern=BEHAVIOUR_KEY_PATTERN),
        ] = None,
        include_archived: IncludeArchived = False,
    ) -> Any:
        """List rule sets, one row per name with its latest version, sha256, behaviour key,
        counts and last validation; archived ones only with include_archived."""
        return await gated_call(
            gate,
            BACKEND_ONLY,
            lambda: client.get(
                "/rule-sets",
                page=page,
                limit=limit,
                behaviour_key=behaviour_key,
                include_archived=include_archived,
            ),
        )

    @mcp.tool()
    async def dataworks_get_rule_set(name: RuleSetName, version: RuleSetVersion = None) -> Any:
        """One rule-set version (default the latest): the document, its sha256 (the pin a recipe
        step uses), its cases, last validation, superseded_by and every build that used it."""
        return await gated_call(
            gate,
            BACKEND_ONLY,
            lambda: client.get(f"/rule-sets/{name}", version=version),
        )

    @mcp.tool()
    async def dataworks_clone_rule_set(
        name: RuleSetName,
        new_name: RuleSetName,
        new_behaviour_key: BehaviourKey,
        version: RuleSetVersion = None,
        substitutions: Annotated[
            list[dict[str, Any]] | None,
            Field(description="[{pointer: JSON Pointer, value: new value}], applied in order."),
        ] = None,
        replacement_cases: Annotated[
            list[dict[str, Any]] | None,
            Field(description="Cases that replace the source's cases of the same id."),
        ] = None,
    ) -> Any:
        """Copy a rule-set version to another name and behaviour key, applying substitutions
        (JSON Pointer -> value) to signals and cases. The copy must pass its cases before it is
        saved; when a substitution breaks a case, the answer lists it and asks for
        replacement_cases, and nothing is saved."""
        return await gated_call(
            gate,
            BACKEND_ONLY,
            lambda: client.post(
                f"/rule-sets/{name}/clone",
                json_body={
                    "version": version,
                    "new_name": new_name,
                    "new_behaviour_key": new_behaviour_key,
                    "substitutions": substitutions or [],
                    "replacement_cases": replacement_cases or [],
                },
            ),
        )

    @mcp.tool()
    async def dataworks_get_rule_set_difference(a: RuleSetRef, b: RuleSetRef) -> Any:
        """The structured difference between two rule-set versions: each changed path (a JSON
        Pointer) with its old and new value; signals and cases are matched by id."""
        return await gated_call(
            gate, BACKEND_ONLY, lambda: client.get("/rule-sets/difference", a=a, b=b)
        )

    @mcp.tool()
    async def dataworks_archive_rule_set(name: RuleSetName, reason: ArchiveReason = None) -> Any:
        """Archive a rule set: hide it (every version) from lists. Nothing is deleted; recipes
        that pin it still build, and dataworks_unarchive_rule_set restores it."""
        return await gated_call(
            gate,
            BACKEND_ONLY,
            lambda: client.post(f"/rule-sets/{name}/archive", json_body=archive_body(reason)),
        )

    @mcp.tool()
    async def dataworks_unarchive_rule_set(name: RuleSetName, reason: ArchiveReason = None) -> Any:
        """Unarchive a rule set: list it again."""
        return await gated_call(
            gate,
            BACKEND_ONLY,
            lambda: client.post(f"/rule-sets/{name}/unarchive", json_body=archive_body(reason)),
        )

    # --- derivations (slice 4) -----------------------------------------------------------------

    @mcp.tool()
    async def dataworks_derive_rule_set(
        source_version_id: Annotated[str, Field(description="The labelled version.")],
        text_column: Annotated[str, Field(description="The column the rules read.")],
        label_column: Annotated[str, Field(description="A column with exactly two classes.")],
        positive_values: Annotated[
            list[str | int | bool], Field(description="The label values counted positive.")
        ],
        behaviour_key: BehaviourKey,
        name: OptionalRuleSetName = None,
        group_column: Annotated[
            str | None,
            Field(description="Rows sharing it stay on one side of the split (default: row key)."),
        ] = None,
        floor: Annotated[float, Field(gt=0, le=1, description="Least held-out precision.")] = 0.95,
        max_signals: Annotated[int, Field(ge=1, description="Most literals to propose.")] = 8,
        min_support: Annotated[int, Field(ge=1, description="Least rows a literal covers.")] = 10,
        held_out_fraction: Annotated[
            float, Field(gt=0, lt=1, description="Share of groups held out for measuring.")
        ] = 0.2,
        seed: Annotated[int, Field(ge=0, description="Seed of the grouped split.")] = 0,
    ) -> Any:
        """Derive a draft rule set from a labelled version (a job; poll dataworks_job_status, then
        read dataworks_get_rule_derivation). The draft is measured on grouped held-out rows only;
        too few held-out rows per class reads "not measured". Nothing is saved until you save
        the draft with dataworks_save_rule_set (its provenance names the source version)."""
        return await gated_call(
            gate,
            BACKEND_ONLY,
            lambda: client.post(
                "/rule-derivations",
                json_body={
                    "source_version_id": source_version_id,
                    "text_column": text_column,
                    "label_column": label_column,
                    "positive_values": positive_values,
                    "behaviour_key": behaviour_key,
                    "name": name,
                    "group_column": group_column,
                    "floor": floor,
                    "max_signals": max_signals,
                    "min_support": min_support,
                    "held_out_fraction": held_out_fraction,
                    "seed": seed,
                },
            ),
        )

    @mcp.tool()
    async def dataworks_get_rule_derivation(
        derivation_id: Annotated[
            str,
            Field(
                description="rdv_... from dataworks_derive_rule_set.", pattern=r"^rdv_[0-9a-f]{24}$"
            ),
        ],
    ) -> Any:
        """A rule derivation: its state (measured, low coverage, not measured), held-out
        precision, recall and coverage per literal and for the draft, the draft document with its
        provenance, and how many source rows contain each literal."""
        return await gated_call(
            gate, BACKEND_ONLY, lambda: client.get(f"/rule-derivations/{derivation_id}")
        )
