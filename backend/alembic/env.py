"""Alembic environment: the sync URL derived from DATABASE_URL, metadata from src.models."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from src.core.config import get_settings
from src.core.database import Base
from src import models  # noqa: F401  (registers every table on Base.metadata)

config = context.config
if config.config_file_name is not None and not config.attributes.get("skip_logging"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def include_name(name, type_, parent_names):  # type: ignore[no-untyped-def]
    """Skip the 32 ``dw_row_events`` partitions (and their indexes): the ORM maps the parent."""
    from src.models.row_event import is_partition_name

    if type_ == "table":
        return not is_partition_name(name)
    if type_ == "index":
        return not is_partition_name(parent_names.get("table_name"))
    return True


def _url() -> str:
    return config.attributes.get("url") or get_settings().database_url_sync


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, compare_type=True, include_name=include_name)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True, include_name=include_name)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
