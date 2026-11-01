"""Feature 017 slice 7 (017-M4): quality gates, detector sets and the send (FTID 017 §4.1, §4.4;
TD-017.29, TD-017.30, TD-017.31; FR-017.48).

- ``dw_versions.visibility_floor TEXT``: ``private_only`` when the recipe (or any input version)
  declares it, else NULL. ``dw_versions_immutable`` compares whole rows, so the column is immutable
  like every other version column.
- ``dw_detector_sets.benchmark_source_ids JSONB NOT NULL DEFAULT '[]'``: the benchmarks the
  operator names for the set's behaviour (contamination as data; the hard-coded catalogue goes).
- ``dw_detector_sends.send_group_id VARCHAR(40)`` with an index: the sends one multi-set send
  started under one approval.
- **Data step (FTID §4.4, I12):** each existing set takes the ``benchmarks[].source_id`` of the
  newest contamination report on any version its roles bind. Reads recorded facts only; names no
  behaviour. A set with no report keeps ``[]``; a report whose ``benchmarks`` is not a list names
  none (Review R4: ``jsonb_array_elements`` over a JSON null failed the whole upgrade).

Downgrades exist for an empty database only (FTDD 002 section 11): refused while a version carries
a floor, a set names benchmarks, or a send belongs to a group.

Revision ID: 0042
Revises: 0041
Create Date: 2026-10-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VERSIONS = "dw_versions"
SETS = "dw_detector_sets"
SENDS = "dw_detector_sends"
FLOOR_CHECK = "visibility_floor IS NULL OR visibility_floor = 'private_only'"
BENCHMARKS_CHECK = "jsonb_typeof(benchmark_source_ids) = 'array'"
#: The newest contamination report over any version a set's roles bind -> its benchmark sources.
DATA_STEP = """
UPDATE dw_detector_sets AS s
SET benchmark_source_ids = found.ids
FROM (
    SELECT DISTINCT ON (r.set_id) r.set_id, COALESCE(
        (SELECT jsonb_agg(DISTINCT b->>'source_id')
         FROM jsonb_array_elements(CASE WHEN jsonb_typeof(vr.result->'benchmarks') = 'array'
                                        THEN vr.result->'benchmarks' ELSE '[]'::jsonb END) AS b
         WHERE b->>'source_id' IS NOT NULL),
        '[]'::jsonb) AS ids
    FROM dw_detector_set_roles AS r
    JOIN dw_version_reports AS vr ON vr.version_id = r.version_id AND vr.kind = 'contamination'
    WHERE vr.state = 'completed' AND jsonb_typeof(vr.result) = 'object'
    ORDER BY r.set_id, vr.created_at DESC, vr.id DESC
) AS found
WHERE s.id = found.set_id
"""


def upgrade() -> None:
    op.add_column(VERSIONS, sa.Column("visibility_floor", sa.Text()))
    op.create_check_constraint(op.f(f"ck_{VERSIONS}_visibility_floor_valid"), VERSIONS, FLOOR_CHECK)
    op.add_column(
        SETS,
        sa.Column(
            "benchmark_source_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_check_constraint(op.f(f"ck_{SETS}_benchmarks_array"), SETS, BENCHMARKS_CHECK)
    op.add_column(SENDS, sa.Column("send_group_id", sa.String(40)))
    op.create_index(op.f(f"ix_{SENDS}_send_group_id"), SENDS, ["send_group_id"])
    op.execute(DATA_STEP)


def downgrade() -> None:
    bind = op.get_bind()
    floors = bind.execute(
        sa.text(f"SELECT count(*) FROM {VERSIONS} WHERE visibility_floor IS NOT NULL")
    ).scalar_one()
    named = bind.execute(
        sa.text(f"SELECT count(*) FROM {SETS} WHERE benchmark_source_ids <> '[]'::jsonb")
    ).scalar_one()
    grouped = bind.execute(
        sa.text(f"SELECT count(*) FROM {SENDS} WHERE send_group_id IS NOT NULL")
    ).scalar_one()
    if floors or named or grouped:
        raise RuntimeError(
            f"{floors} version(s) with a visibility floor, {named} set(s) naming benchmarks and "
            f"{grouped} grouped send(s) exist; dropping 0042 would forget them."
        )
    op.drop_index(op.f(f"ix_{SENDS}_send_group_id"), table_name=SENDS)
    op.drop_column(SENDS, "send_group_id")
    op.drop_constraint(op.f(f"ck_{SETS}_benchmarks_array"), SETS, type_="check")
    op.drop_column(SETS, "benchmark_source_ids")
    op.drop_constraint(op.f(f"ck_{VERSIONS}_visibility_floor_valid"), VERSIONS, type_="check")
    op.drop_column(VERSIONS, "visibility_floor")
