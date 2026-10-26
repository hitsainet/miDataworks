"""Feature 016 slice 3: rule sets travel with the data (FTID 016 section 4.2; FR-016.26).

1. ``dw_publish_files.role`` admits ``rule_set``: a publish uploads each carried rule-set document,
   and the Hub hash comparison (ADR-018) covers it like a split.
2. ``dw_publish_builds.rule_set_files`` (JSONB, NULL = built before 016): the files a build wrote,
   ``{name, version, sha256, behaviour_key, path, bytes, git_blob_sha1}``.
3. ``dw_versions.rule_sets_recorded`` (the "recorded since" marker, FTID I12 decided here): FALSE for
   every version that exists when this migration runs, TRUE (the column default) for every version
   built after it. A version with no use row reads "rule sets not recorded" while the flag is false,
   and ``{recorded: true, rule_sets: []}`` once it is true. A version built between the 0036 and
   0037 deploys that DID run a rule set still lists it (its use rows decide); one that ran none
   reads "not recorded", the honest answer for a version this code could not watch being built.
   Adding the column is DDL, so the immutability trigger of ``dw_versions`` is not involved.

``downgrade`` refuses while a publish file of role ``rule_set`` exists (a publication record).

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FILES = "dw_publish_files"
BUILDS = "dw_publish_builds"
VERSIONS = "dw_versions"


def upgrade() -> None:
    op.drop_constraint(op.f(f"ck_{FILES}_role_valid"), FILES, type_="check")
    op.create_check_constraint(
        op.f(f"ck_{FILES}_role_valid"), FILES, "role IN ('split', 'card', 'manifest', 'rule_set')"
    )
    op.add_column(BUILDS, sa.Column("rule_set_files", postgresql.JSONB()))
    op.add_column(
        VERSIONS,
        sa.Column(
            "rule_sets_recorded", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    op.alter_column(VERSIONS, "rule_sets_recorded", server_default=sa.text("true"))


def downgrade() -> None:
    bind = op.get_bind()
    carried = bind.execute(
        sa.text(f"SELECT count(*) FROM {FILES} WHERE role = 'rule_set'")  # noqa: S608
    ).scalar_one()
    if carried:
        raise RuntimeError(
            f"refusing to downgrade 0037: {carried} published file(s) of role rule_set are "
            "publication records"
        )
    op.drop_column(VERSIONS, "rule_sets_recorded")
    op.drop_column(BUILDS, "rule_set_files")
    op.drop_constraint(op.f(f"ck_{FILES}_role_valid"), FILES, type_="check")
    op.create_check_constraint(
        op.f(f"ck_{FILES}_role_valid"), FILES, "role IN ('split', 'card', 'manifest')"
    )
