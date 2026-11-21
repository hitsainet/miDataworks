"""Endpoint-role routes and Fetch models (ADR-011; Foundation tasks 8.2, 8.3, 8.4).

Feature 014 (operator directives 2026-10-20): keys are kept per origin (``stored_keys``, and
``DELETE /{role}/keys`` removes one); every write needs the Settings PIN when one is set; and EVERY
agent write waits for the operator (``secret_write`` when it carries a key, ``config_write``
otherwise). Before 014 an agent's keyless write ran at once, which would have let any caller that
sent the agent header past the PIN.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import Actor, get_actor, requires_approval_when_agent
from ....core.database import get_db
from ....core.errors import ForbiddenError
from ....core.key_binding import url_origin
from ....core.settings_pin import SETTINGS_PIN
from ....schemas.settings import EndpointRoleOut, EndpointRoleWrite, ModelListOut, StoredKeyOut
from ....services.app_setting_service import is_masked_value
from ....services.endpoint_role_service import (
    KEEP_PRICE,
    EndpointRoleService,
    RoleUpdate,
    RoleView,
)
from ._paging import OptionalPage, Paged, optional_paging, paged_or_all

router = APIRouter(prefix="/api/v1/endpoint-roles", tags=["endpoint-roles"])


def _out(view: RoleView) -> EndpointRoleOut:
    fields = dict(view.__dict__)
    fields["stored_keys"] = [StoredKeyOut(**k.__dict__) for k in view.stored_keys]
    return EndpointRoleOut(**fields)


def _writes_a_key(values: dict[str, object]) -> bool:
    body = values["body"]
    assert isinstance(body, EndpointRoleWrite)
    return body.api_key is not None and body.api_key != "" and not is_masked_value(body.api_key)


def _summary(payload: dict[str, object]) -> str:
    """The approval card's line. Built before the key is removed from the payload, so it names
    the server and never the key."""
    body = payload.get("body")
    body = body if isinstance(body, dict) else {}
    target = url_origin(body.get("base_url")) or "no server"
    key = body.get("api_key")
    if isinstance(key, str) and key and not is_masked_value(key):
        return f"Set the API key of the {payload.get('role')} endpoint (for {target})"
    price = body.get("price_per_call_usd")
    priced = (
        "; clear its price"
        if body.get("clear_price")
        else f"; price ${price} per call" if price is not None else ""
    )
    return f"Change the {payload.get('role')} endpoint (server {target}{priced})"


def _action_for(values: dict[str, object]) -> str:
    """Every agent role write waits for the operator (014): a key is a secret write, anything
    else a configuration write."""
    return "secret_write" if _writes_a_key(values) else "config_write"


@router.get("", response_model=list[EndpointRoleOut] | Paged[EndpointRoleOut])
async def list_roles(
    paging: OptionalPage = Depends(optional_paging), db: AsyncSession = Depends(get_db)
) -> list[EndpointRoleOut] | Paged[EndpointRoleOut]:
    """The four roles, configured or not. Keys are masked. Paged (with the total) when
    ``page``/``limit`` are given."""
    return paged_or_all([_out(v) for v in await EndpointRoleService.list(db)], paging)


@router.get("/{role}", response_model=EndpointRoleOut)
async def get_role(role: str, db: AsyncSession = Depends(get_db)) -> EndpointRoleOut:
    return _out(await EndpointRoleService.get(db, role))


@router.put("/{role}", response_model=EndpointRoleOut, dependencies=[SETTINGS_PIN])
@requires_approval_when_agent(
    "secret_write",
    actions=("config_write",),
    action_for=_action_for,
    secret_fields=("body.api_key",),
    summary=lambda p: _summary(p),
)
async def put_role(
    role: str,
    body: EndpointRoleWrite,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> EndpointRoleOut:
    """Configure one role. A masked key leaves the stored key unchanged; an empty one clears the
    key for this base URL's server. Moving the base URL keeps each server's key for that server."""
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
            price=(
                None
                if body.clear_price
                else KEEP_PRICE if body.price_per_call_usd is None else body.price_per_call_usd
            ),
        ),
    )
    return _out(view)


@router.delete("/{role}/keys", response_model=EndpointRoleOut, dependencies=[SETTINGS_PIN])
async def delete_key(
    role: str,
    origin: str = Query(
        ...,
        max_length=2048,
        description="The server whose key to delete: an origin from stored_keys, or any URL on it",
    ),
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> EndpointRoleOut:
    """Delete the API key stored for one server (in use or kept for later). Operator only."""
    if actor.origin == "agent":
        raise ForbiddenError(
            "Agents cannot delete stored API keys; only the operator can, on the Settings screen.",
            code="AGENT_FORBIDDEN",
        )
    normalised = url_origin(origin) or origin
    return _out(await EndpointRoleService.delete_key(db, role, normalised))


@router.get("/{role}/models", response_model=ModelListOut)
async def fetch_models(
    role: str,
    base_url: str | None = Query(None, max_length=2048, description="Try a URL before saving it"),
    db: AsyncSession = Depends(get_db),
) -> ModelListOut:
    """List the models the role's server serves (OpenAI /v1/models, or TEI /info)."""
    listing = await EndpointRoleService.fetch_models(db, role, base_url)
    return ModelListOut(role=role, models=listing.models, source=listing.source, url=listing.url)
