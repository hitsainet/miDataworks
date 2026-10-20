"""Settings and endpoint-role request and response models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SettingOut(BaseModel):
    key: str
    value: str | None
    is_sensitive: bool
    is_set: bool
    category: str
    description: str
    type: str


class SettingWrite(BaseModel):
    value: str = Field(..., max_length=4096)


class EndpointRoleOut(BaseModel):
    role: str
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
    key_cleared: bool = Field(
        False,
        description="This save moved the endpoint to another server and cleared the stored key",
    )
    key_notice: str | None = Field(
        None, description="Plain-language note about the key (why it was cleared, what to do)"
    )


class EndpointRoleWrite(BaseModel):
    protocol: str | None = None
    base_url: str | None = Field(None, max_length=2048)
    model_id: str | None = Field(None, max_length=255)
    api_key: str | None = Field(
        None,
        max_length=4096,
        description=(
            "null leaves the stored key; empty string clears it; a masked value keeps it. A key "
            "is bound to the URL's origin: moving base_url to another scheme, host or port clears "
            "the stored key unless this request supplies one"
        ),
    )
    inherit_from_judge: bool = False
    use_mode: str = "own"


class ModelListOut(BaseModel):
    role: str
    models: list[str]
    source: str
    url: str
