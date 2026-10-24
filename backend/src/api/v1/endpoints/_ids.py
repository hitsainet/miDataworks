"""Ids that must be UUIDs before they reach the database (005 17.4 live finding, 2026-10-18).

A string bound to a ``Uuid`` column is parsed by asyncpg, not by the API: a short id such as
``2c817685`` raised ``DataError: invalid UUID`` inside the query and the API answered
``500 INTERNAL_ERROR``. Two shapes, matching what the API already did elsewhere:

- a **filter** (``?version_id=``) is typed as a UUID, so FastAPI refuses a malformed value with
  ``422 VALIDATION_ERROR`` before the handler runs (``uuid_filter``);
- a **path** id names one resource, and a malformed one is that resource not found, as
  ``GET /versions/{id}`` and its other sub-routes answer (``version_path_id``).
"""

from __future__ import annotations

import uuid

from ....core.errors import NotFoundError


def uuid_filter(value: uuid.UUID | None) -> str | None:
    """The canonical text of a UUID filter, as the columns store it, or ``None`` when unset."""
    return None if value is None else str(value)


def version_path_id(value: str) -> str:
    """A version id from the path as a canonical UUID, or ``404 version_not_found``."""
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise NotFoundError(
            f"{value!r} is not a valid version id.", code="version_not_found"
        ) from None
