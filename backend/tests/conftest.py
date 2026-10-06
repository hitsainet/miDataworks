"""Shared test configuration (ADR-021; Foundation tasks 11.2, 11.3).

Order matters: the test environment and the per-worker isolation are applied BEFORE anything
calls ``get_settings()``, because settings (and the engines built from them) are cached.

The test database is never the development one: ``_refuse_non_test_database`` stops the session
if ``DATABASE_URL`` names a database without "test" in it, or any miStudio database (miStudio's
memory records eleven phantom failures and polluted development data from that mistake).
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

# --- environment, before any src import that reads settings -----------------------------------
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://midataworks:midataworks-dev@127.0.0.1:55433/midataworks_test",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:56380/15")
os.environ.setdefault("SETTINGS_ENCRYPTION_KEY", "test-only-encryption-key-0123456789abcdef")
os.environ.setdefault("INTERNAL_API_SECRET", "test-only-internal-secret-0123456789")
os.environ.setdefault("DATA_DIR", str(Path(tempfile.gettempdir()) / "midataworks-test-data"))
os.environ["ENVIRONMENT"] = "test"

from tests.support.xdist_isolation import isolate as _isolate_xdist_worker  # noqa: E402

_isolate_xdist_worker()


def _refuse_non_test_database() -> None:
    name = os.environ["DATABASE_URL"].rsplit("/", 1)[-1]
    if "test" not in name or name.startswith("mistudio"):
        raise pytest.UsageError(
            f"DATABASE_URL names {name!r}. Tests run only against a dedicated test database "
            "(a name containing 'test', never a miStudio database)."
        )


_refuse_non_test_database()

from src import models  # noqa: E402,F401
from src.core.database import Base, dispose_engines, get_sync_engine  # noqa: E402


def _provision_and_migrate() -> None:
    """Create this worker's database if missing, reset its schema, migrate to head."""
    from alembic.config import Config
    from sqlalchemy import text

    from alembic import command
    from tests.support.xdist_isolation import provision

    provision(os.environ["DATABASE_URL"])
    engine = get_sync_engine()
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    config.attributes["skip_logging"] = True
    command.upgrade(config, "head")


@pytest.fixture(scope="session")
def migrated_database() -> Iterator[None]:
    _provision_and_migrate()
    yield


@pytest.fixture
def clean_db(migrated_database: None) -> Iterator[None]:
    """Empty every dw_* table before the test."""
    from sqlalchemy import text

    tables = [t.name for t in Base.metadata.sorted_tables]
    with get_sync_engine().begin() as conn:
        conn.execute(text("TRUNCATE " + ", ".join(tables) + " RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private data volume for one test."""
    from src.core.config import get_settings

    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(get_settings(), "data_dir", root)
    return root


@pytest.fixture(autouse=True)
def _reset_progress_throttle() -> Iterator[None]:
    from src.core.cancellation import reset_throttle

    reset_throttle()
    yield
    reset_throttle()


@pytest.fixture
async def client(clean_db: None, data_dir: Path) -> AsyncIterator[object]:
    """An HTTP client against the real FastAPI app (no server, no network)."""
    import httpx

    from src.main import fastapi_app

    transport = httpx.ASGITransport(app=fastapi_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    await dispose_engines()


@pytest.fixture
def operator_name(clean_db: None) -> str:
    """Store an operator name (C5) so UI actions can record who did them."""
    from sqlalchemy import text

    with get_sync_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO dw_app_settings (key, value, is_sensitive, category) "
                "VALUES ('operator_name', 'Test Operator', false, 'identity')"
            )
        )
    return "Test Operator"


def pytest_terminal_summary(terminalreporter, exitstatus, config):  # type: ignore[no-untyped-def]
    """Print the outcome counts at every verbosity (adapted from miStudio's conftest).

    Two ``-q`` flags make ``-qq``, at which pytest drops its own ``N passed`` line, and a run that
    does not say what it counted cannot support "the suite is green". ``write_line`` ignores
    verbosity, so this line survives ``-qq``; it is the number to quote.
    """
    stats = getattr(terminalreporter, "stats", {}) or {}

    def n(key: str) -> int:
        return len(stats.get(key, ()))

    parts = [
        f"{n('passed')} passed",
        f"{n('failed')} failed",
        f"{n('error')} errors",
        f"{n('skipped')} skipped",
    ]
    for key in ("xfailed", "xpassed"):
        if n(key):
            parts.append(f"{n(key)} {key}")
    terminalreporter.write_line(f"SUITE TOTALS: {' | '.join(parts)} (exit status {exitstatus})")
