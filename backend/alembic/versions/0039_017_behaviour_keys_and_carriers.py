"""Feature 017 slice 2 (017-M1): behaviour keys, carrier runs, recorded definitions (FTID 017 §4.1).

1. ``behaviour_key`` (nullable slug, the suite rule copied literally; a partial index ``WHERE
   behaviour_key IS NOT NULL``) on ``dw_recipes`` (the named head row, never the body: recipe
   hashes are unchanged), ``dw_generation_templates``, ``dw_generation_runs``, ``dw_label_runs``
   and ``dw_review_queues`` (FR-017.9). Not unique; existing rows stay NULL (nothing is guessed).
2. ``dw_generation_runs``: modes ``carrier_slot`` and ``carrier_instructed`` (FR-017.14, .15). The
   ``mode`` column widens to 24 characters (``carrier_instructed`` is 18). A detector target is a
   minimal-pair or a carrier run (``detector_mode``, replacing ``detector_is_minimal_pairs``); a
   carrier run has one response, no chosen side, a generator model and a template map, and a slot
   run a slot token (``carrier_shape``). New columns: ``generator_model_id`` (the model sent as the
   request's ``model``; NULL for the older modes, which use the role's model), ``template_map``,
   ``slot_token``, ``requests_in_flight`` (the value in force at start; 1 = today's behaviour) and
   ``responds_to`` (a top-up's earlier version and cells).
3. ``dw_versions.behaviour_definition`` / ``behaviour_configuration`` / ``behaviour_census`` and
   ``dw_detector_sets.behaviour_definition`` (FR-017.11, FR-017.17, FR-017.55): the definition a
   build or a set was made against, the configuration a build used and the census of its rows
   (TD-017.34: persisted with the version). NULL = not a behaviour build.

Downgrades exist for an empty database only (FTDD 002 section 11): it refuses while a carrier run
exists, since dropping the modes would orphan it.

Revision ID: 0039
Revises: 0038
Create Date: 2026-10-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Copied literally (migrations never import app code); equal to ``schemas.behaviour``.
SLUG = "^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$"
SLUG_CHECK = f"behaviour_key IS NULL OR behaviour_key ~ '{SLUG}'"
KEYED = (
    "dw_recipes",
    "dw_generation_templates",
    "dw_generation_runs",
    "dw_label_runs",
    "dw_review_queues",
)

JSONB = postgresql.JSONB(astext_type=sa.Text())
RUNS = "dw_generation_runs"
OLD_MODES = "mode IN ('standard', 'steered_pairs', 'minimal_pairs')"
NEW_MODES = (
    "mode IN ('standard', 'steered_pairs', 'minimal_pairs', 'carrier_slot', 'carrier_instructed')"
)
OLD_DETECTOR = "target_type <> 'detector' OR mode = 'minimal_pairs'"
NEW_DETECTOR = (
    "target_type <> 'detector' OR mode IN ('minimal_pairs', 'carrier_slot', 'carrier_instructed')"
)
CARRIER_SHAPE = (
    "mode NOT IN ('carrier_slot', 'carrier_instructed') OR (n_responses = 1 "
    "AND chosen_side IS NULL AND target_type = 'detector' AND generator_model_id IS NOT NULL "
    "AND template_map IS NOT NULL AND (mode <> 'carrier_slot' OR slot_token IS NOT NULL))"
)


def upgrade() -> None:
    for table in KEYED:
        op.add_column(table, sa.Column("behaviour_key", sa.Text()))
        op.create_check_constraint(op.f(f"ck_{table}_behaviour_key_slug"), table, SLUG_CHECK)
        op.create_index(
            op.f(f"ix_{table}_behaviour_key"),
            table,
            ["behaviour_key"],
            postgresql_where=sa.text("behaviour_key IS NOT NULL"),
        )

    op.alter_column(RUNS, "mode", type_=sa.String(length=24), existing_type=sa.String(length=16))
    op.drop_constraint(op.f(f"ck_{RUNS}_mode_valid"), RUNS, type_="check")
    op.create_check_constraint(op.f(f"ck_{RUNS}_mode_valid"), RUNS, NEW_MODES)
    op.drop_constraint(op.f(f"ck_{RUNS}_detector_is_minimal_pairs"), RUNS, type_="check")
    op.create_check_constraint(op.f(f"ck_{RUNS}_detector_mode"), RUNS, NEW_DETECTOR)
    op.add_column(RUNS, sa.Column("generator_model_id", sa.Text()))
    op.add_column(RUNS, sa.Column("template_map", JSONB))
    op.add_column(RUNS, sa.Column("slot_token", sa.Text()))
    op.add_column(
        RUNS,
        sa.Column("requests_in_flight", sa.SmallInteger(), nullable=False, server_default="1"),
    )
    op.add_column(RUNS, sa.Column("responds_to", JSONB))
    op.create_check_constraint(
        op.f(f"ck_{RUNS}_requests_in_flight_positive"), RUNS, "requests_in_flight >= 1"
    )
    op.create_check_constraint(op.f(f"ck_{RUNS}_carrier_shape"), RUNS, CARRIER_SHAPE)

    op.add_column("dw_versions", sa.Column("behaviour_definition", JSONB))
    op.add_column("dw_versions", sa.Column("behaviour_configuration", JSONB))
    op.add_column("dw_versions", sa.Column("behaviour_census", JSONB))
    op.add_column("dw_detector_sets", sa.Column("behaviour_definition", JSONB))


def downgrade() -> None:
    found = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM dw_generation_runs "
                "WHERE mode IN ('carrier_slot', 'carrier_instructed')"
            )
        )
        .scalar_one()
    )
    if found:
        raise RuntimeError(
            f"{found} carrier generation run(s) exist; dropping the modes would orphan them."
        )
    op.drop_column("dw_detector_sets", "behaviour_definition")
    op.drop_column("dw_versions", "behaviour_census")
    op.drop_column("dw_versions", "behaviour_configuration")
    op.drop_column("dw_versions", "behaviour_definition")
    op.drop_constraint(op.f(f"ck_{RUNS}_carrier_shape"), RUNS, type_="check")
    op.drop_constraint(op.f(f"ck_{RUNS}_requests_in_flight_positive"), RUNS, type_="check")
    for column in (
        "responds_to",
        "requests_in_flight",
        "slot_token",
        "template_map",
        "generator_model_id",
    ):
        op.drop_column(RUNS, column)
    op.drop_constraint(op.f(f"ck_{RUNS}_detector_mode"), RUNS, type_="check")
    op.create_check_constraint(op.f(f"ck_{RUNS}_detector_is_minimal_pairs"), RUNS, OLD_DETECTOR)
    op.drop_constraint(op.f(f"ck_{RUNS}_mode_valid"), RUNS, type_="check")
    op.create_check_constraint(op.f(f"ck_{RUNS}_mode_valid"), RUNS, OLD_MODES)
    op.alter_column(RUNS, "mode", type_=sa.String(length=16), existing_type=sa.String(length=24))
    for table in reversed(KEYED):
        op.drop_index(
            op.f(f"ix_{table}_behaviour_key"),
            table_name=table,
            postgresql_where=sa.text("behaviour_key IS NOT NULL"),
        )
        op.drop_constraint(op.f(f"ck_{table}_behaviour_key_slug"), table, type_="check")
        op.drop_column(table, "behaviour_key")
