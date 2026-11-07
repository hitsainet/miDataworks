# Origin: miStudio (Onegaishimas/miStudio) backend/tests/support/xdist_isolation.py @ c829a2cc
# Mode: adapt (docs/REUSE.md). Changed: only DATABASE_URL is suffixed (the sync URL is derived
# from it in core/config.py, so there is one variable to isolate, not two), DATA_DIR is suffixed
# too so workers never share a data volume, and provisioning uses the asyncpg-free libpq URL.
"""Per-worker isolation for parallel runs, extracted so it can be tested.

`conftest.py` calls this before it imports anything from `src`. It lives in its
own module because the repo's recurring failure is a decision buried where no
test can reach it — the fix that works is to extract the decision into a small
pure function, unit-test it, and have the caller do nothing but call it.

MEASURED (2026-09-25, `tests/unit`, 7,510 tests):

| run                                      | wall    |
|------------------------------------------|---------|
| serial                                   | 16m22s  |
| `-n 8` (xdist default `--dist load`)     | 36m03s  |
| `-n 4 --dist loadfile`, threads pinned   | 4m56s   |

The 8-worker run was **2.2x slower than serial**, so parallelism alone is not the
win; the distribution mode and the thread pinning are. `--dist loadfile` keeps a
file's tests on one worker, so module-level imports are paid once per file
instead of scattered across workers, and pinning BLAS to one thread per worker
stops four torch stacks each trying to use every core.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, MutableMapping

#: Suffixed per worker. Each gets its own database because `async_engine` is
#: function-scoped and drops every table on teardown, so two workers sharing one
#: database delete each other's schema mid-test.
ISOLATED_URL_VARS = ("DATABASE_URL",)

#: Suffixed per worker as a directory, so two workers never write one data volume.
ISOLATED_PATH_VARS = ("DATA_DIR",)

#: NOT suffixed. One shared database kept at alembic head for the schema guards
#: to compare against; tests only read it. Suffixing it would point every worker
#: at a database that was never migrated, and the guards would refuse — which
#: reads as a schema failure rather than as a setup mistake.
SHARED_URL_VARS = ("SCHEMA_CHECK_DATABASE_URL",)

#: One thread per worker. Without this, every worker's torch/numpy stack sizes
#: its pools to the whole machine.
THREAD_VARS = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS")


def worker_database_url(url: str | None, worker: str) -> str | None:
    """The database url this worker should use, or the url unchanged.

    Idempotent: applying it twice does not stack suffixes, which matters because
    conftest can be imported more than once in a session.
    """
    if not url or not worker:
        return url
    suffix = f"_{worker}"
    return url if url.endswith(suffix) else url + suffix


#: Each worker gets its own Redis database (15, 14, 13, ...), so a test that asserts the
#: ephemeral key space is empty cannot see another worker's key (001: a flake in
#: test_agent_token_approval.py under -n 4, 2026-10-07). Idempotent: the database number is
#: derived from the worker, not from the current URL.
ISOLATED_REDIS_VARS = ("REDIS_URL",)


def worker_redis_url(url: str | None, worker: str, base_db: int = 15) -> str | None:
    if not url or not worker:
        return url
    digits = "".join(ch for ch in worker if ch.isdigit())
    index = int(digits) if digits else 0
    db = max(1, base_db - index)
    base, slash, last = url.rpartition("/")
    return f"{base}/{db}" if slash and last.isdigit() else f"{url.rstrip('/')}/{db}"


def plan_worker_environment(env: Mapping[str, str]) -> dict[str, str]:
    """What must change in the environment for this worker. Empty when serial.

    Pure: takes an environment, returns the overrides. The caller applies them.
    """
    worker = env.get("PYTEST_XDIST_WORKER", "")
    if not worker:
        return {}

    overrides: dict[str, str] = {}
    for var in ISOLATED_URL_VARS:
        isolated = worker_database_url(env.get(var), worker)
        if isolated and isolated != env.get(var):
            overrides[var] = isolated
    for var in ISOLATED_PATH_VARS:
        isolated = worker_database_url(env.get(var), worker)
        if isolated and isolated != env.get(var):
            overrides[var] = isolated
    for var in ISOLATED_REDIS_VARS:
        # A second worktree running its suite at the same time sets REDIS_TEST_DB_BASE lower, so
        # the two runs' worker databases do not overlap (feature 003's worktree uses 11..8).
        isolated = worker_redis_url(env.get(var), worker, int(env.get("REDIS_TEST_DB_BASE", "15")))
        if isolated and isolated != env.get(var):
            overrides[var] = isolated
    for var in THREAD_VARS:
        if not env.get(var):  # never override a value the operator set
            overrides[var] = "1"
    return overrides


def libpq_dsn(url: str) -> str:
    """Strip a SQLAlchemy driver suffix, because psycopg2 takes a libpq URI.

    CI sets `DATABASE_URL_SYNC=postgresql+psycopg2://…` while this workstation
    sets plain `postgresql://…`, so passing the url straight to
    `psycopg2.connect` works locally and fails only in CI — the shape of bug
    this repo keeps paying for. Normalising here means provisioning behaves the
    same in both places.
    """
    scheme, separator, rest = url.partition("://")
    return f"{scheme.split('+', 1)[0]}{separator}{rest}"


def provision(sync_url: str) -> None:
    """Create this worker's database if it does not exist.

    Postgres has no CREATE DATABASE IF NOT EXISTS, and doing it here rather than
    in a documented setup step is what keeps a parallel run working on a fresh
    clone and in CI without an extra job step.
    """
    import psycopg2
    from psycopg2 import errors as pg_errors

    base, _, dbname = libpq_dsn(sync_url).rpartition("/")
    connection = psycopg2.connect(f"{base}/postgres")
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            try:
                cursor.execute(f'CREATE DATABASE "{dbname}"')
            except pg_errors.DuplicateDatabase:
                pass
    finally:
        connection.close()


def isolate(env: MutableMapping[str, str] | None = None) -> dict[str, str]:
    """Apply the plan to the environment and provision the database.

    Returns the overrides applied, so a caller (or a test) can see what happened.
    Must run BEFORE `get_settings()` is first called: settings are cached, so a
    rewrite performed later would never reach the engines.
    """
    environment = os.environ if env is None else env
    overrides = plan_worker_environment(environment)
    environment.update(overrides)
    if "DATABASE_URL" in overrides:
        provision(overrides["DATABASE_URL"])
    return overrides
