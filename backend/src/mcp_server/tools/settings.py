"""Category ``settings``: masked settings, the Hugging Face token and endpoint keys (P-11), and the
runtime environment settings (feature 012: read them; change or reset one through approval).

The secret writes, and clearing a secret, wait for the operator when an agent calls them
(``secret_write``). The secret
arguments are named in ``SECRET_PARAMS`` so the audit line never carries them. The operator's own
name (``operator_name``) has no tool: it is the operator's "who" (C5), refused at REST for agents.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from ..client import DataworksClient
from ..context import READ, ToolContext
from ..health_gate import gated_call

#: A Settings field name as the routes take it (lower snake case).
_SETTING_KEY = Field(
    description="The setting's key, as dataworks_get_environment lists it (for example "
    "endpoint_http_timeout_seconds).",
    min_length=1,
    max_length=128,
    pattern=r"^[a-z][a-z0-9_]*$",
)
_REASON = Field(description="Why, kept in the settings history (optional).", max_length=500)


def register(mcp: MCPServer, client: DataworksClient, ctx: ToolContext) -> None:
    gate = ctx.gate

    @mcp.tool()
    async def dataworks_get_settings() -> Any:
        """Every setting an agent may read. Secrets are masked; the operator's name is withheld."""
        return await gated_call(gate, READ, lambda: client.get("/settings"))

    @mcp.tool()
    async def dataworks_set_hf_token(
        token: Annotated[
            str,
            Field(
                description="The Hugging Face access token (hf_...).", min_length=1, max_length=512
            ),
        ],
    ) -> Any:
        """Store the Hugging Face token used for gated imports and publishing. ALWAYS waits for
        the operator's approval when called by an agent (secret_write): returns {"approval_id",
        "status": "pending", ...}. The token is stored encrypted and never returned."""
        return await gated_call(
            gate, READ, lambda: client.put("/settings/hf_token", json_body={"value": token})
        )

    @mcp.tool()
    async def dataworks_delete_setting(
        key: Annotated[
            str,
            Field(
                description="The setting to clear, as named by dataworks_get_settings.",
                min_length=1,
                max_length=128,
                pattern=r"^[A-Za-z0-9_.-]+$",
            ),
        ],
    ) -> Any:
        """Clear a stored setting (operator decision, 2026-10-07). Clearing a SECRET (a token or
        an API key) ALWAYS waits for the operator's approval when called by an agent
        (secret_write) and returns {"approval_id", "status": "pending", ...}; the operator's
        name is refused for agents (C5). A key that is not set is a 404 SETTING_NOT_SET."""
        return await gated_call(gate, READ, lambda: client.delete(f"/settings/{key}"))

    @mcp.tool()
    async def dataworks_set_endpoint_key(
        role: Annotated[
            Literal["classifier", "judge", "generation", "embeddings"],
            Field(description="The endpoint role to configure."),
        ],
        protocol: Annotated[
            str | None,
            Field(description="Wire protocol of the role's server; keep the current value."),
        ],
        base_url: Annotated[
            str | None,
            Field(description="Server URL (http:// or https://); keep the current value."),
        ],
        model_id: Annotated[
            str | None, Field(description="Model to use; keep the current value.", max_length=255)
        ],
        api_key: Annotated[
            str | None,
            Field(
                description=(
                    "The API key. null keeps the stored keys; an empty string clears the key "
                    "for base_url's server. Keys are stored per server and a key is sent only "
                    "to the server it was entered for: a base_url on another server keeps the "
                    "old key for the old server and uses the key stored for the new one, if any."
                ),
                max_length=4096,
            ),
        ] = None,
        inherit_from_judge: Annotated[
            bool, Field(description="generation and embeddings only: use the judge's endpoint.")
        ] = False,
        use_mode: Annotated[
            Literal["own", "same_as_classifier", "none"],
            Field(description="judge only: own endpoint, same as classifier, or none."),
        ] = "own",
    ) -> Any:
        """Set a role's API key. The backend REPLACES the role's whole configuration, so pass the
        role's current protocol, base_url and model_id (read them with
        dataworks_list_endpoint_roles); null clears them. The call ALWAYS waits for the operator's
        approval: secret_write when api_key is a real key, config_write otherwise."""
        body = {
            "protocol": protocol,
            "base_url": base_url,
            "model_id": model_id,
            "api_key": api_key,
            "inherit_from_judge": inherit_from_judge,
            "use_mode": use_mode,
        }
        return await gated_call(
            gate, READ, lambda: client.put(f"/endpoint-roles/{role}", json_body=body)
        )

    @mcp.tool()
    async def dataworks_get_environment() -> Any:
        """Every environment setting with the value in force, the environment's value, where the
        value comes from (env or override), when a change takes effect, and whether an agent may
        change it; plus the last 50 changes. Groups: runtime (changeable while miDataworks runs),
        restart (needs the backend pod restarted), approval_rule (deployment configuration, read-
        only) and environment (read-only). Secrets are shown only as set or not set."""
        return await gated_call(gate, READ, lambda: client.get("/settings/environment"))

    @mcp.tool()
    async def dataworks_set_environment(
        key: Annotated[str, _SETTING_KEY],
        value: Annotated[
            str,
            Field(
                description="The new value as text: a number, true or false, an address, or an "
                "empty string for 'not set' where the setting allows it.",
                max_length=4096,
            ),
        ],
        reason: Annotated[str | None, _REASON] = None,
    ) -> Any:
        """Change a runtime environment setting (group runtime, agent_writable true). ALWAYS waits
        for the operator's approval when called by an agent (config_write): returns
        {"approval_id", "status": "pending", ...}. The value is checked against the setting's
        bounds before anything is queued (422 INVALID_SETTING_VALUE). Operator-only settings
        (miLLM and miStudio addresses, the steering switch) are refused (403 AGENT_FORBIDDEN);
        other groups are not writable here (422 NOT_RUNTIME_WRITABLE)."""
        body: dict[str, Any] = {"value": value}
        if reason:
            body["reason"] = reason
        return await gated_call(gate, READ, lambda: client.put(f"/settings/{key}", json_body=body))

    @mcp.tool()
    async def dataworks_reset_environment(
        key: Annotated[str, _SETTING_KEY],
        reason: Annotated[str | None, _REASON] = None,
    ) -> Any:
        """Return a runtime environment setting to its environment value (removes the
        override). ALWAYS waits for the operator's approval when called by an agent
        (config_write). A setting with no override is a 404 SETTING_NOT_SET."""
        return await gated_call(
            gate, READ, lambda: client.delete(f"/settings/{key}", reason=reason)
        )
