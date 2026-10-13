"""Endpoint-role routes and Fetch models (ADR-011; Foundation tasks 8.2, 8.3, 8.4)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import Actor, get_actor, requires_approval_when_agent
from ....core.database import get_db
from ....schemas.settings import EndpointRoleOut, EndpointRoleWrite, ModelListOut
from ....services.app_setting_service import is_masked_value
from ....services.endpoint_role_service import EndpointRoleService, RoleUpdate, RoleView

router = APIRouter(prefix="/api/v1/endpoint-roles", tags=["endpoint-roles"])


def _out(view: RoleView) -> EndpointRoleOut:
    return EndpointRoleOut(**view.__dict__)


def _writes_a_key(values: dict[str, object], _db: AsyncSession) -> bool:
    """An agent's role write is gated only when it carries a real key (not absent or masked)."""
    body = values["body"]
    assert isinstance(body, EndpointRoleWrite)
    return body.api_key is not None and body.api_key != "" and not is_masked_value(body.api_key)


@router.get("", response_model=list[EndpointRoleOut])
async def list_roles(db: AsyncSession = Depends(get_db)) -> list[EndpointRoleOut]:
    """The four roles, configured or not. Keys are masked."""
    return [_out(v) for v in await EndpointRoleService.list(db)]


@router.get("/{role}", response_model=EndpointRoleOut)
async def get_role(role: str, db: AsyncSession = Depends(get_db)) -> EndpointRoleOut:
    return _out(await EndpointRoleService.get(db, role))


@router.put("/{role}", response_model=EndpointRoleOut)
@requires_approval_when_agent(
    "secret_write",
    when=_writes_a_key,
    secret_fields=("body.api_key",),
    summary=lambda p: f"Set the API key of the {p.get('role')} endpoint",
)
async def put_role(
    role: str,
    body: EndpointRoleWrite,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> EndpointRoleOut:
    """Configure one role. A masked key leaves the stored key unchanged; an empty one clears it."""
    view = await EndpointRoleService.update(
        db,
        role,
        RoleUpdate(
            protocol=body.protocol,
            base_url=body.base_url,
            model_id=body.model_id,
            api_key=body.api_key,
            inherit_from_judge=body.inherit_from_judge,
            use_mode=body.use_mode,
        ),
    )
    return _out(view)


@router.get("/{role}/models", response_model=ModelListOut)
async def fetch_models(
    role: str,
    base_url: str | None = Query(None, max_length=2048, description="Try a URL before saving it"),
    db: AsyncSession = Depends(get_db),
) -> ModelListOut:
    """List the models the role's server serves (OpenAI /v1/models, or TEI /info)."""
    listing = await EndpointRoleService.fetch_models(db, role, base_url)
    return ModelListOut(role=role, models=listing.models, source=listing.source, url=listing.url)
