"""``dw_endpoint_role_keys``: the keyring of API keys not in use (feature 014; operator directive
2026-10-20).

A role's key is stored for the ORIGIN (scheme, host, port) it was entered for. The key for the
role's current origin lives on ``dw_endpoint_roles`` (``api_key_ciphertext`` and
``api_key_origin``), where every sender already reads it; a key for any other origin waits here.
When the role's base URL moves, ``EndpointRoleService.update`` parks the key in use under its
origin and brings back the key stored for the new origin, if there is one. So each key lives in
exactly one place, and moving a role to miLLM and back to api.typesafe.ai restores the TypeSafe key
without it ever being sent to miLLM.

Migration ``0034``. Rows go when their role row goes (``ON DELETE CASCADE``).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base


class EndpointRoleKey(Base):
    __tablename__ = "dw_endpoint_role_keys"
    __table_args__ = (CheckConstraint("length(origin) > 0", name="origin_not_empty"),)

    role: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("dw_endpoint_roles.role", ondelete="CASCADE"),
        primary_key=True,
    )
    #: ``scheme://host:port`` (``core/key_binding.url_origin``): the only server this key goes to.
    origin: Mapped[str] = mapped_column(Text, primary_key=True)
    #: AES-GCM envelope, exactly as it was on the role row (never decrypted to move it).
    api_key_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    stored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
