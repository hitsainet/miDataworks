"""The endpoint-role contract: one function turns a role into an endpoint (FR-005.3 – FR-005.5).

Rules (FTDD 005 section 6.1; FPRD 005 section 7.4 item 3):

- ``classifier`` uses its own row.
- ``judge``: ``use = own`` its own row; ``same_as_classifier`` takes the base URL and key from the
  classifier and keeps the JUDGE's protocol and model (handoff section 4a); ``none`` is
  unconfigured.
- ``generation`` and ``embeddings`` take base URL, key and model from the resolved judge unless set
  separately; their protocols are fixed by role (chat completions; ``/v1/embeddings`` or TEI).

A role that resolves to no base URL or no model raises :class:`RoleUnconfigured` naming the
Settings field to fill. Every feature calls :func:`resolve` (004 embeddings, 007 generation, 009
probe verdicts, 003's Data Designer relay through ``operators/endpoint_port``); an AST test forbids
reading ``dw_endpoint_roles`` for resolution anywhere else. Keys are decrypted here and held in
memory only (``repr=False``); they are never logged or persisted.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..core.encryption import decrypt_value
from ..core.errors import AppError
from ..models.endpoint_role import INHERITING_ROLES, ROLES, EndpointRole

#: Protocols fixed by role (FR-005.4). ``embeddings`` keeps a stored choice between the two.
FIXED_PROTOCOL: dict[str, str] = {"generation": "openai_chat"}


class _RoleRow(Protocol):
    role: str
    protocol: str | None
    base_url: str | None
    model_id: str | None
    api_key_ciphertext: str | None
    inherit_from_judge: bool
    use_mode: str


class RoleUnconfigured(AppError):
    """``409 ROLE_UNCONFIGURED``; ``details.setting`` names the Settings field."""

    status_code = 409
    code = "ROLE_UNCONFIGURED"


@dataclass(frozen=True)
class ResolvedRoleEndpoint:
    role: str
    protocol: str
    base_url: str
    model_id: str
    api_key: str | None = field(default=None, repr=False)
    #: The role whose base URL and key this endpoint uses, when not its own.
    inherited_from: str | None = None

    def snapshot(self) -> dict[str, str | None]:
        """What a run records about its endpoint: NEVER the key."""
        return {
            "role": self.role,
            "protocol": self.protocol,
            "base_url": self.base_url,
            "model_id": self.model_id,
            "inherited_from": self.inherited_from,
        }


def _unconfigured(role: str, field_name: str, why: str) -> RoleUnconfigured:
    return RoleUnconfigured(
        f"The {role} endpoint is not configured: {why}. Fill in Settings → Endpoints → {role} → "
        f"{field_name.replace('_', ' ')}.",
        details={"role": role, "setting": f"endpoint_roles.{role}.{field_name}"},
    )


def _key(row: _RoleRow | None) -> str | None:
    if row is None or row.api_key_ciphertext is None:
        return None
    return decrypt_value(row.api_key_ciphertext, setting_key=f"endpoint_role:{row.role}")


def resolve_from_rows(role: str, rows: Mapping[str, _RoleRow]) -> ResolvedRoleEndpoint:
    """The pure resolution rule over the stored rows."""
    if role not in ROLES:
        raise AppError(f"{role!r} is not an endpoint role.", code="UNKNOWN_ROLE", status_code=422)
    row = rows.get(role)

    if role == "classifier":
        return _own(role, row)

    if role == "judge":
        if row is None:
            raise _unconfigured(role, "base_url", "no judge endpoint is set")
        if row.use_mode == "none":
            raise _unconfigured(role, "use_mode", "the judge's Use is set to none")
        if row.use_mode == "same_as_classifier":
            classifier = rows.get("classifier")
            if classifier is None or not classifier.base_url:
                raise _unconfigured(
                    "classifier", "base_url", "the judge uses the classifier's endpoint"
                )
            if not row.model_id:
                raise _unconfigured(role, "model_id", "no judge model is chosen")
            return ResolvedRoleEndpoint(
                role,
                row.protocol or "openai_chat",
                classifier.base_url,
                row.model_id,
                _key(classifier),
                "classifier",
            )
        return _own(role, row, default_protocol="openai_chat")

    # generation, embeddings
    assert role in INHERITING_ROLES
    if row is not None and not row.inherit_from_judge:
        return _own(
            role,
            row,
            default_protocol=FIXED_PROTOCOL.get(role, "openai_embeddings"),
            fixed=FIXED_PROTOCOL.get(role),
        )
    try:
        judge = resolve_from_rows("judge", rows)
    except RoleUnconfigured:
        raise _unconfigured(
            role, "base_url", "it inherits the judge's endpoint, and the judge has none"
        ) from None
    model = row.model_id if row is not None and row.model_id else judge.model_id
    protocol = FIXED_PROTOCOL.get(role) or (
        row.protocol if row is not None and row.protocol else "openai_embeddings"
    )
    return ResolvedRoleEndpoint(
        role, protocol, judge.base_url, model, judge.api_key, judge.inherited_from or "judge"
    )


def _own(
    role: str,
    row: _RoleRow | None,
    *,
    default_protocol: str | None = None,
    fixed: str | None = None,
) -> ResolvedRoleEndpoint:
    if row is None or not row.base_url:
        raise _unconfigured(role, "base_url", "no endpoint URL is set")
    if not row.model_id:
        raise _unconfigured(role, "model_id", "no model is chosen")
    protocol = fixed or row.protocol or default_protocol
    if protocol is None:
        raise _unconfigured(role, "protocol", "no protocol is chosen")
    return ResolvedRoleEndpoint(role, protocol, row.base_url, row.model_id, _key(row))


def resolve(role: str, session: Session | None = None) -> ResolvedRoleEndpoint:
    """Resolve through a sync session (workers, the operator port)."""
    if session is not None:
        rows = {r.role: r for r in session.execute(select(EndpointRole)).scalars()}
        return resolve_from_rows(role, rows)
    from ..core.database import get_sync_db

    with get_sync_db() as db:
        rows = {r.role: r for r in db.execute(select(EndpointRole)).scalars()}
        return resolve_from_rows(role, rows)


async def resolve_async(db: AsyncSession, role: str) -> ResolvedRoleEndpoint:
    """Resolve through the API's async session."""
    rows = {r.role: r for r in (await db.execute(select(EndpointRole))).scalars()}
    return resolve_from_rows(role, rows)
