"""Endpoint roles: storage, inheritance, Fetch models (ADR-011; Foundation tasks 8.2–8.4).

Inheritance (handoff section 4a):
- the judge's ``use_mode`` is ``own``, ``same_as_classifier`` or ``none``;
- ``generation`` and ``embeddings`` use the judge's endpoint while ``inherit_from_judge`` is set.

Keys are encrypted at rest and only ever shown masked. An update carrying a masked key leaves
the ciphertext byte-identical; an empty string clears it; ``None`` leaves it alone.

A key is bound to the origin (scheme, host, port) of the base URL it was entered with
(``core/key_binding.py``, operator decision 2026-10-20). An update that moves the base URL to
another origin without supplying a new key CLEARS the stored key and says so in ``key_notice``:
the key belonged to the old server, and switching back needs it entered again.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients.model_listing import ModelListing, fetch_models
from ..core.encryption import DecryptionError, decrypt_value, encrypt_value, mask_value
from ..core.errors import AppError, UnprocessableError
from ..core.key_binding import key_for, url_origin
from ..models.endpoint_role import INHERITING_ROLES, JUDGE_USE, PROTOCOLS, ROLES, EndpointRole
from .app_setting_service import is_masked_value


@dataclass(frozen=True)
class RoleView:
    role: str
    configured: bool
    protocol: str | None
    base_url: str | None
    model_id: str | None
    api_key: str | None  # masked
    has_api_key: bool
    #: The origin the stored key is bound to (it is sent nowhere else).
    key_origin: str | None
    inherit_from_judge: bool
    use_mode: str
    #: Where requests for this role actually go, after inheritance.
    effective_role: str | None
    #: Set on the answer to an update that cleared the key because the base URL moved.
    key_cleared: bool = False
    key_notice: str | None = None


@dataclass(frozen=True)
class RoleUpdate:
    protocol: str | None
    base_url: str | None
    model_id: str | None
    api_key: str | None
    inherit_from_judge: bool
    use_mode: str


def _check_role(role: str) -> None:
    if role not in ROLES:
        raise UnprocessableError(
            f"{role!r} is not an endpoint role. Roles: {', '.join(ROLES)}.", code="UNKNOWN_ROLE"
        )


def _masked(row: EndpointRole) -> str | None:
    if row.api_key_ciphertext is None:
        return None
    try:
        return mask_value(
            decrypt_value(row.api_key_ciphertext, setting_key=f"endpoint_role:{row.role}")
        )
    except DecryptionError:
        return "***"


def effective_role(role: str, rows: dict[str, EndpointRole]) -> str | None:
    """The role whose endpoint serves ``role``, after inheritance; None when it has none."""
    seen: set[str] = set()
    current = role
    while current not in seen:
        seen.add(current)
        row = rows.get(current)
        if current in INHERITING_ROLES and (row is None or row.inherit_from_judge):
            current = "judge"
            continue
        if current == "judge" and row is not None and row.use_mode == "same_as_classifier":
            current = "classifier"
            continue
        if current == "judge" and row is not None and row.use_mode == "none":
            return None
        if row is None or not row.base_url:
            return None
        return current
    return None


class EndpointRoleService:
    @staticmethod
    async def _rows(db: AsyncSession) -> dict[str, EndpointRole]:
        return {r.role: r for r in (await db.execute(select(EndpointRole))).scalars()}

    @staticmethod
    def _view(role: str, rows: dict[str, EndpointRole]) -> RoleView:
        row = rows.get(role)
        if row is None:
            return RoleView(
                role,
                False,
                None,
                None,
                None,
                None,
                False,
                None,
                role in INHERITING_ROLES,
                "own",
                effective_role(role, rows),
            )
        return RoleView(
            role,
            bool(row.base_url),
            row.protocol,
            row.base_url,
            row.model_id,
            _masked(row),
            row.api_key_ciphertext is not None,
            row.api_key_origin if row.api_key_ciphertext is not None else None,
            row.inherit_from_judge,
            row.use_mode,
            effective_role(role, rows),
        )

    @staticmethod
    async def list(db: AsyncSession) -> list[RoleView]:
        rows = await EndpointRoleService._rows(db)
        return [EndpointRoleService._view(role, rows) for role in ROLES]

    @staticmethod
    async def get(db: AsyncSession, role: str) -> RoleView:
        _check_role(role)
        return EndpointRoleService._view(role, await EndpointRoleService._rows(db))

    @staticmethod
    async def update(db: AsyncSession, role: str, data: RoleUpdate) -> RoleView:
        _check_role(role)
        if data.protocol is not None and data.protocol not in PROTOCOLS[role]:
            raise UnprocessableError(
                f"{data.protocol!r} is not a protocol for the {role} role. "
                f"Choose one of: {', '.join(PROTOCOLS[role])}.",
                code="UNKNOWN_PROTOCOL",
            )
        if data.inherit_from_judge and role not in INHERITING_ROLES:
            raise UnprocessableError(
                "Only generation and embeddings can inherit the judge's endpoint.",
                code="INHERIT_NOT_ALLOWED",
            )
        if data.use_mode not in JUDGE_USE or (role != "judge" and data.use_mode != "own"):
            raise UnprocessableError(
                "Only the judge has a Use setting (own, same_as_classifier or none).",
                code="USE_MODE_NOT_ALLOWED",
            )
        if (
            data.base_url is not None
            and data.base_url
            and not data.base_url.startswith(("http://", "https://"))
        ):
            raise UnprocessableError(
                "The endpoint URL must start with http:// or https://.", code="BAD_URL"
            )

        row = await db.get(EndpointRole, role)
        if row is None:
            row = EndpointRole(role=role)
            db.add(row)
        new_base_url = data.base_url or None
        new_origin = url_origin(new_base_url)
        row.protocol = data.protocol
        row.base_url = new_base_url
        row.model_id = data.model_id or None
        row.inherit_from_judge = data.inherit_from_judge
        row.use_mode = data.use_mode
        supplies_key = (
            data.api_key is not None and data.api_key != "" and not is_masked_value(data.api_key)
        )
        if data.api_key is not None:
            if data.api_key == "":
                row.api_key_ciphertext = None
                row.api_key_origin = None
            elif is_masked_value(data.api_key):
                if row.api_key_ciphertext is None:
                    raise AppError(
                        "That looks like a masked key, and no key is stored to keep. Paste the real key.",
                        code="MASKED_VALUE_WITHOUT_SECRET",
                        status_code=422,
                    )
                # masked value: keep the ciphertext byte-identical (task 8.4)
            else:
                row.api_key_ciphertext = encrypt_value(data.api_key)
                row.api_key_origin = new_origin
        old_origin = row.api_key_origin
        cleared_notice: str | None = None
        if not supplies_key and row.api_key_ciphertext is not None and old_origin != new_origin:
            # The key belongs to the server it was entered for: a move clears it (2026-10-20).
            row.api_key_ciphertext = None
            row.api_key_origin = None
            cleared_notice = (
                f"The stored API key for the {role} endpoint was cleared because the endpoint "
                f"moved from {old_origin or 'no server'} to {new_origin or 'no server'}. A key is "
                "only ever sent to the server it was entered for. Enter the key again to use one "
                "here; switching back to the old server also needs its key entered again."
            )
        await db.commit()
        view = await EndpointRoleService.get(db, role)
        if cleared_notice is not None:
            view = replace(view, key_cleared=True, key_notice=cleared_notice)
        return view

    @staticmethod
    async def fetch_models(
        db: AsyncSession, role: str, base_url_override: str | None
    ) -> ModelListing:
        _check_role(role)
        rows = await EndpointRoleService._rows(db)
        target = effective_role(role, rows)
        base_url = base_url_override
        api_key: str | None = None
        source_row = rows.get(target) if target else rows.get(role)
        if base_url is None:
            if source_row is None or not source_row.base_url:
                raise UnprocessableError(
                    f"The {role} role has no endpoint URL. Enter one, then fetch models.",
                    code="ROLE_UNCONFIGURED",
                )
            base_url = source_row.base_url
        # The stored key goes only to the origin it was entered for. Sending it to an override URL
        # on another server would hand the key to whatever server the caller names.
        key_origin: str | None = None
        if source_row is not None and source_row.api_key_ciphertext is not None:
            key_origin = source_row.api_key_origin
            api_key = key_for(
                base_url,
                decrypt_value(
                    source_row.api_key_ciphertext, setting_key=f"endpoint_role:{source_row.role}"
                ),
                key_origin,
            )
        return await fetch_models(base_url, api_key, key_origin=key_origin)
