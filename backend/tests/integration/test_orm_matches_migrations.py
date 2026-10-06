"""The ORM metadata matches the migrated schema on a real PostgreSQL 15 (Foundation task 3.5).

Adapted from miStudio's ORM-versus-migration check: Alembic's own comparison runs between
``Base.metadata`` and a database migrated to head. Any difference — a column added to a model with
no migration, a type that drifted, an index missing — is a failure.
"""

from __future__ import annotations

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text

from src.core.database import Base, get_sync_engine


def test_orm_matches_the_migrated_schema(migrated_database: None) -> None:
    with get_sync_engine().connect() as conn:
        version = conn.execute(
            text("SELECT current_setting('server_version_num')::int / 10000")
        ).scalar_one()
        context = MigrationContext.configure(conn, opts={"compare_type": True})
        diff = compare_metadata(context, Base.metadata)
    assert version == 15, f"the schema check must run on PostgreSQL 15, got {version}"
    assert diff == [], f"ORM and migrations disagree: {diff}"


def test_every_dw_table_is_migrated(migrated_database: None) -> None:
    with get_sync_engine().connect() as conn:
        tables = set(
            conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            ).scalars()
        )
    assert {t.name for t in Base.metadata.sorted_tables} <= tables
    assert all(name.startswith("dw_") for name in Base.metadata.tables), "ADR-003 table prefix"
