"""Endpoint keys kept per origin, and the Settings PIN (feature 014; operator directives 2026-10-20).

Numbered 0034: it follows 0033 (runtime settings), so the chain stays one head.

1. ``dw_endpoint_role_keys``: the keys of a role for origins it does not point at now (role,
   origin, ciphertext). The key for the role's current origin stays on ``dw_endpoint_roles``,
   where every sender reads it; a move parks it here and brings back the key stored for the new
   origin. Nothing is moved by this migration: until now a move CLEARED the key (migration 0031's
   rule), so no parked key exists to carry over.
2. ``dw_settings_pin``: one row at most, the PBKDF2 hash of the Settings PIN. No PIN is set by the
   migration: Settings stays open until the operator sets one.

Downgrade drops both tables. Parked keys are lost (0031's rule had none), and so is the PIN.

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KEYS = "dw_endpoint_role_keys"
PIN = "dw_settings_pin"


def upgrade() -> None:
    op.create_table(
        KEYS,
        sa.Column(
            "role",
            sa.String(32),
            sa.ForeignKey(
                "dw_endpoint_roles.role",
                name=op.f(f"fk_{KEYS}_role_dw_endpoint_roles"),
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("api_key_ciphertext", sa.Text(), nullable=False),
        sa.Column(
            "stored_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("role", "origin", name=op.f(f"pk_{KEYS}")),
        sa.CheckConstraint("length(origin) > 0", name=op.f(f"ck_{KEYS}_origin_not_empty")),
    )
    op.create_table(
        PIN,
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("pin_hash", sa.Text(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{PIN}")),
        sa.CheckConstraint("id = 1", name=op.f(f"ck_{PIN}_single_row")),
    )


def downgrade() -> None:
    op.drop_table(PIN)
    op.drop_table(KEYS)
