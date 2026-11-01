"""Settings and endpoint-role request and response models."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class SettingOut(BaseModel):
    key: str
    value: str | None
    is_sensitive: bool
    is_set: bool
    category: str
    description: str
    type: str
    #: Feature 012: for a runtime environment setting, where the value in force comes from.
    source: Literal["env", "override"] = "env"
    env_value: str | None = Field(
        None, description="The environment's value of a runtime setting, as text (empty: not set)"
    )
    effect: str | None = Field(None, description="When a change takes effect")
    runtime_writable: bool = False


class SettingWrite(BaseModel):
    value: str = Field(..., max_length=4096)
    reason: str | None = Field(
        None, max_length=500, description="Why, kept in the settings history (optional)"
    )

    @field_validator("value", mode="before")
    @classmethod
    def _scalar_as_text(cls, value: Any) -> Any:
        """A JSON number or boolean is accepted and stored as the text Settings parses."""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, int | float):
            return str(value)
        if value is None:
            return ""
        return value


class SettingChangeOut(BaseModel):
    key: str
    previous_value: str | None = Field(None, description="None: there was no override")
    new_value: str | None = Field(None, description="None: back to the environment value")
    actor: str
    origin: str
    approval_id: str | None
    reason: str | None
    changed_at: datetime


class EnvironmentRow(BaseModel):
    key: str
    env_var: str
    label: str
    description: str
    group: Literal["runtime", "restart", "approval_rule", "environment"]
    type: str
    value: Any = Field(None, description="The value in force (secrets: never; see is_set)")
    env_value: Any = Field(None, description="The environment's value (secrets: never)")
    source: Literal["env", "override"]
    runtime_writable: bool
    agent_writable: bool
    effect: str | None = None
    effect_detail: str | None = None
    restart: str | None = Field(None, description="What to restart, for the restart group")
    caution: str | None = None
    secret: bool = False
    is_set: bool = True
    minimum: float | None = None
    maximum: float | None = None
    exclusive_minimum: float | None = None
    exclusive_maximum: float | None = None
    choices: list[str] | None = None
    updated_at: datetime | None = None
    updated_by: str | None = None


class EnvironmentOut(BaseModel):
    settings: list[EnvironmentRow]
    history: list[SettingChangeOut]


class StoredKeyOut(BaseModel):
    origin: str = Field(description="scheme://host:port this key goes to, and nowhere else")
    api_key: str = Field(description="Masked; the key itself is never returned")
    in_use: bool = Field(description="True for the key of the role's current base URL")


class EndpointRoleOut(BaseModel):
    role: str
    #: 017 TD-017.44: the price per call (None: not priced) and whether runs on it are paid.
    price_per_call_usd: float | None = None
    paid: bool = False
    configured: bool
    protocol: str | None
    base_url: str | None
    model_id: str | None
    api_key: str | None = Field(None, description="Masked; the key itself is never returned")
    has_api_key: bool
    key_origin: str | None = Field(
        None,
        description="scheme://host:port the stored key was entered for; it is sent nowhere else",
    )
    inherit_from_judge: bool
    use_mode: str
    effective_role: str | None
    stored_keys: list[StoredKeyOut] = Field(
        default_factory=list,
        description=(
            "Every API key stored for this role, one per server (origin), masked. A key is sent "
            "only to its own origin; the one for the current base URL is in use"
        ),
    )
    key_restored: bool = Field(
        False,
        description="This save moved the endpoint to a server whose key was stored earlier; it is in use again",
    )
    key_parked: bool = Field(
        False,
        description="This save moved the endpoint away; the key in use was kept for its own server",
    )
    key_notice: str | None = Field(
        None, description="Plain-language note about what a move did to the keys"
    )


class EndpointRoleWrite(BaseModel):
    protocol: str | None = None
    base_url: str | None = Field(None, max_length=2048)
    model_id: str | None = Field(None, max_length=255)
    api_key: str | None = Field(
        None,
        max_length=4096,
        description=(
            "null leaves the stored keys; empty string clears the key for this base_url's server; "
            "a masked value keeps it. Keys are stored per server (origin): moving base_url to "
            "another scheme, host or port keeps the old key for the old server and uses the key "
            "stored for the new one, if any"
        ),
    )
    inherit_from_judge: bool = False
    use_mode: str = "own"
    #: 017 TD-017.44: US dollars per call (one row, one question), up to six decimals. null (or
    #: omitted) keeps the stored price, as ``api_key`` does; ``clear_price`` removes it. Moving
    #: base_url to another server clears a kept price.
    price_per_call_usd: Decimal | None = Field(
        None,
        ge=0,
        max_digits=18,
        decimal_places=6,
        description=(
            "US dollars per call (one row, one question). null keeps the stored price; "
            "clear_price removes it; moving base_url to another server clears it. A hosted "
            "protocol with no price is treated as paid and refused until a price, zero "
            "included, is entered"
        ),
    )
    clear_price: bool = False


class ModelListOut(BaseModel):
    role: str
    models: list[str]
    source: str
    url: str
