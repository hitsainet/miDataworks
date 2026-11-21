"""Database engines and sessions (ADR-002; Foundation task 3.3).

Two session styles, as in miWorkbench: an async engine for the API and sync sessions for Celery
workers. Both use :data:`SESSION_AUTOFLUSH`, and the test fixtures import the same constant —
miWorkbench recorded a mutation control that passed against a defect because its fixture used
``autoflush=True`` while production used ``False``.

Engines are built lazily on first use rather than at import, so a test (or an xdist worker)
can point ``DATABASE_URL`` at its own database before anything connects.

**Numeric work never runs on a greenlet.** ``AsyncSession.run_sync`` runs its function on one. For a
chained temporary of 256 KiB or more, numpy walks the C stack with glibc's ``backtrace()`` to decide
whether it may reuse the array, and on that greenlet the walk can segfault the whole API process
(exit 139; a set check on the SSN pilot, 2026-11-01: stubbing ``backtrace`` alone stopped it). Routes
that compute use :func:`run_in_own_session`; :func:`off_greenlet` guards a shared funnel below them.
"""

from __future__ import annotations

import contextvars
from collections.abc import AsyncIterator, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any

from fastapi.concurrency import run_in_threadpool
from greenlet import getcurrent  # type: ignore[import-untyped]
from sqlalchemy import Engine, MetaData, create_engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool

from .config import get_settings

#: The autoflush setting for every session, in production and in tests.
SESSION_AUTOFLUSH = False

#: Deterministic constraint names, so Alembic autogenerate and the ORM-versus-migration check
#: compare like with like.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for every ``dw_*`` table."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


_async_engine: AsyncEngine | None = None
_async_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_sync_engine: Engine | None = None
_sync_sessionmaker: sessionmaker[Session] | None = None


def get_async_engine() -> AsyncEngine:
    global _async_engine, _async_sessionmaker
    if _async_engine is None:
        settings = get_settings()
        # Tests run each test in its own event loop; a pooled asyncpg connection is bound to the
        # loop that opened it, so the test environment opens a connection per checkout.
        if settings.environment == "test":
            _async_engine = create_async_engine(settings.database_url, poolclass=NullPool)
        else:
            _async_engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        _async_sessionmaker = async_sessionmaker(
            _async_engine, expire_on_commit=False, autoflush=SESSION_AUTOFLUSH
        )
    return _async_engine


def get_sync_engine() -> Engine:
    global _sync_engine, _sync_sessionmaker
    if _sync_engine is None:
        _sync_engine = create_engine(get_settings().database_url_sync, pool_pre_ping=True)
        _sync_sessionmaker = sessionmaker(
            _sync_engine, expire_on_commit=False, autoflush=SESSION_AUTOFLUSH
        )
    return _sync_engine


def async_session_factory() -> async_sessionmaker[AsyncSession]:
    get_async_engine()
    assert _async_sessionmaker is not None
    return _async_sessionmaker


def sync_session_factory() -> sessionmaker[Session]:
    get_sync_engine()
    assert _sync_sessionmaker is not None
    return _sync_sessionmaker


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, committed on success, rolled back on error."""
    async with async_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


async def run_in_own_session[T](fn: Callable[[Session], T]) -> T:
    """For a route: ``fn`` on its own sync session in a worker thread, committed on success. The
    event loop keeps serving meanwhile, and numeric work is off the greenlet ``run_sync`` would use.
    It does not see what the request's async session has not committed."""

    def call() -> T:
        with sync_session_factory()() as session:
            result = fn(session)
            session.commit()
            return result

    return await run_in_threadpool(call)


def off_greenlet[T](fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Call ``fn``, in a plain thread when on a greenlet, and wait. Its arguments must not need the
    greenlet: a session from ``run_sync`` does, a sync session of its own does not."""
    if getcurrent().parent is None:
        return fn(*args, **kwargs)
    context = contextvars.copy_context()
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="off-greenlet") as pool:
        return pool.submit(context.run, fn, *args, **kwargs).result()


@contextmanager
def get_sync_db() -> Iterator[Session]:
    """A worker session. The caller commits; anything uncommitted is rolled back on exit."""
    session = sync_session_factory()()
    try:
        yield session
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


async def dispose_engines() -> None:
    """Close both engines (tests and shutdown)."""
    global _async_engine, _async_sessionmaker, _sync_engine, _sync_sessionmaker
    if _async_engine is not None:
        await _async_engine.dispose()
    if _sync_engine is not None:
        _sync_engine.dispose()
    _async_engine = _async_sessionmaker = None
    _sync_engine = _sync_sessionmaker = None
