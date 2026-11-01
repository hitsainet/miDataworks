"""Endpoint roles: storage, inheritance, Fetch models (ADR-011; Foundation tasks 8.2–8.4).

Inheritance (handoff section 4a):
- the judge's ``use_mode`` is ``own``, ``same_as_classifier`` or ``none``;
- ``generation`` and ``embeddings`` use the judge's endpoint while ``inherit_from_judge`` is set.

Keys are encrypted at rest and only ever shown masked. An update carrying a masked key leaves
the ciphertext byte-identical; an empty string clears it; ``None`` leaves it alone.

A key is bound to the origin (scheme, host, port) of the base URL it was entered with
(``core/key_binding.py``, operator decision 2026-10-20). Keys are kept PER ORIGIN (feature 014,
operator directive 2026-10-20, replacing the earlier "clear on move"): the key for the role's
current origin is on the role row, where every sender reads it, and keys for other origins wait in
``dw_endpoint_role_keys``. An update that moves the base URL parks the key in use under its origin
and brings back the key stored for the new origin, if any, and says so in ``key_notice``. So
pointing the classifier at miLLM never sends the TypeSafe key there, and pointing it back at
api.typesafe.ai sends the TypeSafe key again without it being re-entered.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients.model_listing import ModelListing, fetch_models
from ..core.encryption import DecryptionError, decrypt_value, encrypt_value, mask_value
from ..core.errors import AppError, UnprocessableError
from ..core.key_binding import key_for, url_origin
from ..models.endpoint_role import INHERITING_ROLES, JUDGE_USE, PROTOCOLS, ROLES, EndpointRole
from ..models.endpoint_role_key import EndpointRoleKey
from .app_setting_service import is_masked_value
from .labeling.cost import is_paid


@dataclass(frozen=True)
class StoredKey:
    """One stored key of a role: the server it goes to, masked, and whether it is in use."""

    origin: str
    api_key: str  # masked
    in_use: bool


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
    #: Every key stored for this role, one per origin; the one for the current origin is in use.
    stored_keys: tuple[StoredKey, ...] = field(default_factory=tuple)
    #: On the answer to an update that moved the base URL: the key stored for the new origin was
    #: brought back (``key_restored``) and/or the key in use was kept for its old origin
    #: (``key_parked``). ``key_notice`` says which, in words.
    key_restored: bool = False
    key_parked: bool = False
    key_notice: str | None = None
    #: 017 TD-017.44: the operator's price per call in US dollars (None: not priced), and whether
    #: a run on this role counts as paid (``services/labeling/cost.is_paid``).
    price_per_call_usd: float | None = None
    paid: bool = False


#: ``RoleUpdate.price``: leave the stored price as it is (the request did not name one).
KEEP_PRICE = object()


@dataclass(frozen=True)
class RoleUpdate:
    protocol: str | None
    base_url: str | None
    model_id: str | None
    api_key: str | None
    inherit_from_judge: bool
    use_mode: str
    #: 017: a Decimal sets the price per call, None clears it, ``KEEP_PRICE`` leaves it. A move to
    #: another server clears a kept price (a price belongs to the server it was entered for).
    price: Any = KEEP_PRICE


def _check_role(role: str) -> None:
    if role not in ROLES:
        raise UnprocessableError(
            f"{role!r} is not an endpoint role. Roles: {', '.join(ROLES)}.", code="UNKNOWN_ROLE"
        )


def _mask_ciphertext(ciphertext: str, role: str) -> str:
    try:
        return mask_value(decrypt_value(ciphertext, setting_key=f"endpoint_role:{role}"))
    except DecryptionError:
        return "***"


def _masked(row: EndpointRole) -> str | None:
    if row.api_key_ciphertext is None:
        return None
    return _mask_ciphertext(row.api_key_ciphertext, row.role)


def _stored_keys(row: EndpointRole | None, parked: list[EndpointRoleKey]) -> tuple[StoredKey, ...]:
    keys: list[StoredKey] = []
    if row is not None and row.api_key_ciphertext is not None and row.api_key_origin:
        keys.append(StoredKey(row.api_key_origin, _masked(row) or "***", True))
    keys.extend(
        StoredKey(k.origin, _mask_ciphertext(k.api_key_ciphertext, k.role), False) for k in parked
    )
    return tuple(sorted(keys, key=lambda k: (not k.in_use, k.origin)))


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
    async def _parked(db: AsyncSession) -> dict[str, list[EndpointRoleKey]]:
        out: dict[str, list[EndpointRoleKey]] = {}
        for key in (await db.execute(select(EndpointRoleKey))).scalars():
            out.setdefault(key.role, []).append(key)
        return out

    @staticmethod
    def _view(
        role: str,
        rows: dict[str, EndpointRole],
        parked: dict[str, list[EndpointRoleKey]] | None = None,
    ) -> RoleView:
        row = rows.get(role)
        stored = _stored_keys(row, (parked or {}).get(role, []))
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
                stored,
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
            stored,
            price_per_call_usd=(
                None if row.price_per_call_usd is None else float(row.price_per_call_usd)
            ),
            paid=is_paid(row.protocol, row.price_per_call_usd),
        )

    @staticmethod
    async def list(db: AsyncSession) -> list[RoleView]:
        rows = await EndpointRoleService._rows(db)
        parked = await EndpointRoleService._parked(db)
        return [EndpointRoleService._view(role, rows, parked) for role in ROLES]

    @staticmethod
    async def get(db: AsyncSession, role: str) -> RoleView:
        _check_role(role)
        return EndpointRoleService._view(
            role, await EndpointRoleService._rows(db), await EndpointRoleService._parked(db)
        )

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

        new_base_url = data.base_url or None
        new_origin = url_origin(new_base_url)
        supplies_key = (
            data.api_key is not None and data.api_key != "" and not is_masked_value(data.api_key)
        )
        if supplies_key and new_origin is None:
            raise UnprocessableError(
                "Enter the endpoint URL together with its API key: a key is stored for the server "
                "it is entered for, and is sent nowhere else.",
                code="KEY_NEEDS_URL",
            )

        row = await db.get(EndpointRole, role)
        if row is None:
            row = EndpointRole(role=role)
            db.add(row)
            await db.flush()  # the keyring's foreign key needs the role row
        had_any_key = (
            row.api_key_ciphertext is not None
            or (
                await db.execute(select(EndpointRoleKey.origin).where(EndpointRoleKey.role == role))
            ).first()
            is not None
        )
        old_key_origin = row.api_key_origin if row.api_key_ciphertext is not None else None
        moved = row.api_key_ciphertext is not None and old_key_origin != new_origin

        old_origin = url_origin(row.base_url)
        if data.price is not KEEP_PRICE:
            row.price_per_call_usd = data.price
        elif old_origin != new_origin:
            row.price_per_call_usd = None  # 017: the price was for the server it was entered for
        row.protocol = data.protocol
        row.base_url = new_base_url
        row.model_id = data.model_id or None
        row.inherit_from_judge = data.inherit_from_judge
        row.use_mode = data.use_mode

        # 1. The key in use belongs to its own origin: a move parks it there (never discards it).
        parked_origin: str | None = None
        if moved:
            assert row.api_key_ciphertext is not None
            if old_key_origin is not None:
                await EndpointRoleService._park(db, role, old_key_origin, row.api_key_ciphertext)
                parked_origin = old_key_origin
            # A key with no origin was never sendable (migration 0031): nothing to keep it for.
            row.api_key_ciphertext = None
            row.api_key_origin = None
        # 2. The key stored for the new origin, if any, comes back into use.
        restored = False
        if row.api_key_ciphertext is None and new_origin is not None:
            waiting = await db.get(EndpointRoleKey, (role, new_origin))
            if waiting is not None:
                row.api_key_ciphertext = waiting.api_key_ciphertext
                row.api_key_origin = new_origin
                await db.delete(waiting)
                restored = True
        # 3. The key in the request applies to the NEW origin only.
        if data.api_key is not None:
            if data.api_key == "":
                row.api_key_ciphertext = None
                row.api_key_origin = None
                restored = False
            elif is_masked_value(data.api_key):
                if not had_any_key:
                    raise AppError(
                        "That looks like a masked key, and no key is stored to keep. Paste the real key.",
                        code="MASKED_VALUE_WITHOUT_SECRET",
                        status_code=422,
                    )
                # masked value: keep what is stored, byte-identical (task 8.4)
            else:
                row.api_key_ciphertext = encrypt_value(data.api_key)
                row.api_key_origin = new_origin
                restored = False
                # The new key replaces any key waiting for this origin.
                await db.execute(
                    delete(EndpointRoleKey).where(
                        EndpointRoleKey.role == role, EndpointRoleKey.origin == new_origin
                    )
                )
        notice = _move_notice(role, parked_origin, new_origin, restored, row)
        await db.commit()
        view = await EndpointRoleService.get(db, role)
        if notice is not None:
            view = replace(
                view,
                key_restored=restored,
                key_parked=parked_origin is not None,
                key_notice=notice,
            )
        return view

    @staticmethod
    async def _park(db: AsyncSession, role: str, origin: str, ciphertext: str) -> None:
        existing = await db.get(EndpointRoleKey, (role, origin))
        if existing is None:
            db.add(EndpointRoleKey(role=role, origin=origin, api_key_ciphertext=ciphertext))
        else:
            existing.api_key_ciphertext = ciphertext
        await db.flush()

    @staticmethod
    async def delete_key(db: AsyncSession, role: str, origin: str) -> RoleView:
        """Delete the key stored for one origin, in use or waiting. ``404`` when none is."""
        _check_role(role)
        row = await db.get(EndpointRole, role)
        deleted = False
        if row is not None and row.api_key_ciphertext is not None and row.api_key_origin == origin:
            row.api_key_ciphertext = None
            row.api_key_origin = None
            deleted = True
        waiting = await db.get(EndpointRoleKey, (role, origin))
        if waiting is not None:
            await db.delete(waiting)
            deleted = True
        if not deleted:
            raise AppError(
                f"No API key is stored for {origin} on the {role} endpoint.",
                code="KEY_NOT_STORED",
                status_code=404,
            )
        await db.commit()
        return await EndpointRoleService.get(db, role)

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
        # With the keyring (feature 014) a key stored for the override's origin is that origin's
        # own key, so it is the one sent; still checked by key_for here and in fetch_models.
        key_origin: str | None = None
        ciphertext: str | None = None
        target_origin = url_origin(base_url)
        if source_row is not None:
            if (
                source_row.api_key_ciphertext is not None
                and source_row.api_key_origin == target_origin
            ):
                ciphertext, key_origin = source_row.api_key_ciphertext, source_row.api_key_origin
            elif target_origin is not None:
                waiting = await db.get(EndpointRoleKey, (source_row.role, target_origin))
                if waiting is not None:
                    ciphertext, key_origin = waiting.api_key_ciphertext, waiting.origin
        if ciphertext is not None:
            api_key = key_for(
                base_url,
                decrypt_value(ciphertext, setting_key=f"endpoint_role:{source_row.role}"),  # type: ignore[union-attr]
                key_origin,
            )
        return await fetch_models(base_url, api_key, key_origin=key_origin)


def _move_notice(
    role: str,
    parked_origin: str | None,
    new_origin: str | None,
    restored: bool,
    row: EndpointRole,
) -> str | None:
    """What a move did to the keys, in words; None when the update moved nothing."""
    if parked_origin is None and not restored:
        return None
    parts: list[str] = []
    if parked_origin is not None:
        parts.append(
            f"The API key for {parked_origin} is kept for that server and is sent only there; "
            "it comes back if you point this endpoint at it again."
        )
    if restored:
        parts.append(f"The {role} endpoint now uses the key stored earlier for {new_origin}.")
    elif row.api_key_ciphertext is None:
        parts.append(
            f"No API key is stored for {new_origin or 'this endpoint'}, so requests go without "
            "one until you enter a key for it."
        )
    return " ".join(parts)
