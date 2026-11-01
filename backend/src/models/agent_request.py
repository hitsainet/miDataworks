"""``dw_agent_requests``: one row per agent-originated REST request (010 FTDD section 4.2; T-51).

Feeds the Agent access card's activity line (FR-010.33). ``route`` is the route TEMPLATE
(``/api/v1/versions/{version_id}``), never the concrete path, so IDs do not accumulate and no
argument value is stored. Pruned after 30 days by ``midataworks.system.prune_agent_requests``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base


class AgentRequest(Base):
    __tablename__ = "dw_agent_requests"
    __table_args__ = (
        CheckConstraint("length(identity) > 0", name="identity_present"),
        Index("ix_dw_agent_requests_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    identity: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    route: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
