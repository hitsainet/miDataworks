"""Features 001, 002, 003, 004 and 009's migrations reverse cleanly on an empty database (002 FTASKS 3.6).

Downgrades exist for an empty database only: versions are permanent records (FTDD 002 section 11).
The test downgrades this worker's database to Foundation's head, checks every new table, trigger and
function is gone, and migrates back to head in ``finally`` so the session's schema is restored even
when an assertion fails.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import text

from alembic import command
from src.core.database import get_sync_engine

FOUNDATION_HEAD = "0004"
NEW_TABLES = {
    "dw_sources",
    "dw_source_files",
    "dw_source_annotations",
    "dw_datasets",
    "dw_recipe_bodies",
    "dw_recipes",
    "dw_recipe_revisions",
    "dw_recipe_drafts",
    "dw_step_executions",
    "dw_versions",
    "dw_version_inputs",
    "dw_version_steps",
    "dw_version_builds",
    "dw_version_verifications",
    "dw_version_comparisons",
    "dw_row_events",
    "dw_agent_label_rows",
    "dw_operator_allowlist",
    "dw_version_reports",
    "dw_shortcut_levels",
    "dw_detector_sets",
    "dw_detector_set_roles",
    "dw_detector_sends",
    "dw_detector_send_steps",
    "dw_mistudio_registrations",
    "dw_detector_results",
    "dw_reward_marks",
    "dw_agreement_reports",
    "dw_length_profiles",
    "dw_reproduction_links",
}


def _config() -> Config:
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.attributes["skip_logging"] = True
    return config


def _tables_and_functions() -> tuple[set[str], set[str]]:
    with get_sync_engine().connect() as conn:
        tables = set(
            conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            ).scalars()
        )
        functions = set(
            conn.execute(
                text(
                    "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
                    " WHERE n.nspname = 'public' AND p.proname LIKE 'dw\\_%'"
                )
            ).scalars()
        )
    return tables, functions


def test_downgrade_to_foundation_and_back(clean_db: None) -> None:
    config = _config()
    try:
        command.downgrade(config, FOUNDATION_HEAD)
        tables, functions = _tables_and_functions()
        assert not (tables & NEW_TABLES), sorted(tables & NEW_TABLES)
        assert not any(t.startswith("dw_row_events_p") for t in tables)
        assert not functions, sorted(functions)
        assert "dw_jobs" in tables
    finally:
        command.upgrade(config, "head")
    tables, functions = _tables_and_functions()
    assert NEW_TABLES <= tables
    assert {
        "dw_versions_immutable",
        "dw_sources_guard",
        "dw_append_only",
        "dw_reproduction_link_unused",
    } <= functions
