"""Binding resolvers: which recorded model-output runs a build may consume (FR-002.2, C-002.8).

A build binds label runs (feature 005) and generation runs (feature 007) by ID, so model steps
never call an endpoint during a build. Each owning feature registers a resolver for its kind with
:func:`register_binding_resolver`. Until it does, a binding of that kind is refused with
``binding_not_found`` naming the missing feature — never accepted on trust.

A resolver returns ``None`` when the run does not exist, or a mapping with ``complete`` (bool) and
anything the build should record (``fingerprint``, ``rows``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import AppError, NotFoundError

Resolver = Callable[[AsyncSession, str], Awaitable[dict[str, Any] | None]]

#: kind -> resolver. Populated by features 005 and 007 at import.
BINDING_RESOLVERS: dict[str, Resolver] = {}

OWNERS = {"label_run": "feature 005 (Labeling)", "generation_run": "feature 007 (Generation)"}


def register_binding_resolver(kind: str, resolver: Resolver) -> None:
    BINDING_RESOLVERS[kind] = resolver


async def resolve_bindings(
    db: AsyncSession, bindings: list[dict[str, str]]
) -> list[dict[str, Any]]:
    """Resolve each binding, sorted by (kind, id); refuse the first that cannot be used."""
    resolved: list[dict[str, Any]] = []
    for binding in sorted(bindings, key=lambda b: (b["kind"], b["id"])):
        kind, run_id = binding["kind"], binding["id"]
        resolver = BINDING_RESOLVERS.get(kind)
        if resolver is None:
            raise NotFoundError(
                f"Cannot bind {kind} {run_id}: {OWNERS.get(kind, kind)} is not installed, so no "
                f"{kind} exists to bind.",
                code="binding_not_found",
                details={"kind": kind, "id": run_id},
            )
        record = await resolver(db, run_id)
        if record is None:
            raise NotFoundError(
                f"No {kind} {run_id}. Check the run ID.",
                code="binding_not_found",
                details={"kind": kind, "id": run_id},
            )
        if not record.get("complete", False):
            raise AppError(
                f"{kind} {run_id} has not completed; a build binds only finished runs.",
                code="binding_incomplete",
                status_code=409,
                details={"kind": kind, "id": run_id},
            )
        resolved.append({"kind": kind, "id": run_id})
    return resolved
