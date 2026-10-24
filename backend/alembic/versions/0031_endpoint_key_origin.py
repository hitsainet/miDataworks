"""An endpoint role's API key records the origin it was entered for (operator decision 2026-10-20).

``dw_endpoint_roles.api_key_origin``: ``scheme://host:port`` of the role's base URL when its key was
stored. Senders send the key only to that origin (``src/core/key_binding.py``); Settings clears the
key when the base URL moves to another origin without a new key.

Backfill: every row that holds a key is bound to the origin of its CURRENT base URL, so an existing
key keeps working where it is configured today and goes nowhere else. A row with a key and no
(usable) base URL stays NULL, and a key with no origin is never sent. The origin is computed here
in Python with a frozen copy of ``key_binding.url_origin`` (a migration must not import ``src``);
``tests/integration/test_migration_0031.py`` pins the copy to the live function.

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-20
"""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlsplit

import sqlalchemy as sa

from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "dw_endpoint_roles"
_DEFAULT_PORTS = {"http": 80, "https": 443}


def origin_of(url: str | None) -> str | None:
    """Frozen copy of ``src.core.key_binding.url_origin`` (2026-10-20)."""
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if not scheme or not host:
        return None
    if port is None:
        port = _DEFAULT_PORTS.get(scheme)
    if port is None:
        return None
    if ":" in host:
        host = f"[{host}]"
    return f"{scheme}://{host}:{port}"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("api_key_origin", sa.Text()))
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            f"SELECT role, base_url FROM {TABLE} WHERE api_key_ciphertext IS NOT NULL"  # noqa: S608
        )
    ).all()
    for role, base_url in rows:
        origin = origin_of(base_url)
        if origin is not None:
            conn.execute(
                sa.text(f"UPDATE {TABLE} SET api_key_origin = :o WHERE role = :r"),  # noqa: S608
                {"o": origin, "r": role},
            )


def downgrade() -> None:
    op.drop_column(TABLE, "api_key_origin")
