"""``dw_endpoint_roles``: one row per role, never an endpoint list (ADR-011; task 8.2).

miStudio kept endpoints as one undifferentiated list, and a model setting filed under it appeared
as a saved endpoint whose delete button removed the labeling model. Here the role is the primary
key: there are at most four rows, and each one says what it is for.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base

ROLES: tuple[str, ...] = ("classifier", "judge", "generation", "embeddings")
#: Roles that may inherit the judge's endpoint (handoff section 4a).
INHERITING_ROLES: frozenset[str] = frozenset({"generation", "embeddings"})
#: Protocols per role (ADR-011). Feature 005 builds the clients.
PROTOCOLS: dict[str, tuple[str, ...]] = {
    "classifier": ("openai_scoring", "tei_classification", "plugin"),
    "judge": ("openai_chat",),
    "generation": ("openai_chat",),
    "embeddings": ("openai_embeddings", "tei_embeddings"),
}
#: The judge's "Use" setting (handoff section 4a): its own endpoint, the classifier's, or none.
JUDGE_USE: tuple[str, ...] = ("own", "same_as_classifier", "none")


class EndpointRole(Base):
    __tablename__ = "dw_endpoint_roles"
    __table_args__ = (
        CheckConstraint(
            "role IN ('classifier', 'judge', 'generation', 'embeddings')", name="role_valid"
        ),
        CheckConstraint(
            "inherit_from_judge = false OR role IN ('generation', 'embeddings')",
            name="inherit_only_generation_embeddings",
        ),
        CheckConstraint("use_mode IN ('own', 'same_as_classifier', 'none')", name="use_mode_valid"),
    )

    role: Mapped[str] = mapped_column(String(32), primary_key=True)
    protocol: Mapped[str | None] = mapped_column(String(64))
    base_url: Mapped[str | None] = mapped_column(Text)
    model_id: Mapped[str | None] = mapped_column(String(255))
    #: AES-GCM envelope of the API key; never returned by the API (masked only).
    api_key_ciphertext: Mapped[str | None] = mapped_column(Text)
    inherit_from_judge: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Meaningful for the judge only; other roles keep "own".
    use_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="own")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
