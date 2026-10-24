# Origin: miStudio (Onegaishimas/miStudio) backend/tests/unit/test_entrypoint_schema_wait.py @ 67b9ab1f
# Mode: adapt (docs/REUSE.md). The entrypoint half lives in tests/unit/test_backend_image.py (this
# repo's stub-executable harness for docker-entrypoint.sh); here are the manifest pin and
# `python -m src.core.migrate wait-for-head` run for real against a scratch PostgreSQL database.
"""Worker and beat containers wait for the schema before they start.

miStudio's INCIDENT of 2026-10-21, whose exposure miDataworks shared: the pod's worker and beat
containers start at the same moment as the API container (one pod, strategy Recreate). While the
API's migration was blocked, they were already querying a column that migration adds, and failing
with "column does not exist". ``docker-entrypoint.sh`` now runs ``wait-for-head`` before a worker
or beat starts (tests/unit/test_backend_image.py runs it against stub executables).

MUTATION CONTROLS (2026-10-21; each alone, restored from saved bytes, sha256-checked) — all red;
the record is 0xcc/reviews/migrate_safely_2026-10-21.md:
  W1 the entrypoint's wait_for_schema call removed (worker and beat)
  W2 the wait's failure ignored (`|| true`)
  W3 the API migrates with a bare `alembic upgrade head` again
  W4 schema_state reports "behind" as head
  W5 the poll loop gives up after one look
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy.engine import make_url

BACKEND = Path(__file__).resolve().parents[2]
MANIFESTS = BACKEND.parent / "k8s" / "base"


class TestTheManifest:
    def test_no_worker_or_beat_container_opts_out_of_the_wait(self) -> None:
        maps = {
            d["metadata"]["name"]: d.get("data") or {}
            for d in yaml.safe_load_all((MANIFESTS / "config.yaml").read_text())
            if d and d.get("kind") == "ConfigMap"
        }
        seen = []
        for doc in yaml.safe_load_all((MANIFESTS / "backend.yaml").read_text()):
            if not doc or doc.get("kind") != "Deployment":
                continue
            for container in doc["spec"]["template"]["spec"]["containers"]:
                env: dict[str, Any] = {}
                for source in container.get("envFrom", []):
                    env.update(maps.get(source.get("configMapRef", {}).get("name"), {}))
                env.update({e["name"]: e.get("value") for e in container.get("env", [])})
                if env.get("SERVICE_TYPE") in ("worker", "beat"):
                    seen.append(container["name"])
                    assert env.get("WAIT_FOR_SCHEMA", "true") != "false", container["name"]
        assert "beat" in seen and len(seen) >= 2, seen


# ----------------------------------------------------------------------------- wait-for-head, for real


def _server_url() -> Any:
    return make_url(os.environ["DATABASE_URL"])


def _connect(dbname: str) -> Any:
    import psycopg2

    u = _server_url()
    conn = psycopg2.connect(
        host=u.host or "localhost",
        port=u.port or 5432,
        user=u.username,
        password=u.password,
        dbname=dbname,
        connect_timeout=5,
    )
    conn.autocommit = True
    return conn


@pytest.fixture
def scratch_db() -> Iterator[tuple[str, Any]]:
    name = f"schema_wait_test_{uuid.uuid4().hex[:12]}"
    admin = _connect(_server_url().database)
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    conn = _connect(name)
    try:
        yield name, conn
    finally:
        conn.close()
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


@pytest.fixture(scope="module")
def head_and_previous() -> tuple[str, str]:
    from alembic.script import ScriptDirectory

    from src.core.migrate import DEFAULT_INI, _config

    script = ScriptDirectory.from_config(_config(DEFAULT_INI))
    (head,) = script.get_heads()
    previous = script.get_revision(head).down_revision
    assert isinstance(previous, str)
    return str(head), previous


def _stamp(conn: Any, revision: str | None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) PRIMARY KEY)"
        )
        cur.execute("DELETE FROM alembic_version")
        if revision:
            cur.execute("INSERT INTO alembic_version VALUES (%s)", (revision,))


def _wait_cmd(dbname: str, timeout: float) -> tuple[list[str], dict[str, str]]:
    url = _server_url().set(database=dbname).render_as_string(hide_password=False)
    env = dict(
        os.environ,
        DATABASE_URL=url,
        MIGRATION_WAIT_TIMEOUT=str(timeout),
        MIGRATION_WAIT_POLL="0.2",
        PYTHONUNBUFFERED="1",
    )
    return [sys.executable, "-m", "src.core.migrate", "wait-for-head"], env


def _wait(dbname: str, timeout: float) -> tuple[subprocess.CompletedProcess[str], float]:
    cmd, env = _wait_cmd(dbname, timeout)
    started = time.monotonic()
    result = subprocess.run(
        cmd, cwd=BACKEND, env=env, capture_output=True, text=True, timeout=timeout + 60
    )
    return result, time.monotonic() - started


class TestWaitForHead:
    def test_a_database_at_head_starts_at_once(
        self, scratch_db: tuple[str, Any], head_and_previous: tuple[str, str]
    ) -> None:
        dbname, conn = scratch_db
        _stamp(conn, head_and_previous[0])
        result, elapsed = _wait(dbname, timeout=30)
        assert result.returncode == 0 and "schema is at head" in result.stderr, result.stderr
        assert elapsed < 25

    def test_a_database_behind_the_code_is_waited_for(
        self, scratch_db: tuple[str, Any], head_and_previous: tuple[str, str]
    ) -> None:
        dbname, conn = scratch_db
        _stamp(conn, head_and_previous[1])
        result, elapsed = _wait(dbname, timeout=2)
        assert result.returncode == 1, result.stderr
        assert elapsed >= 2 and "GIVING UP" in result.stderr and "Not starting" in result.stderr

    def test_a_database_with_no_alembic_version_is_waited_for(
        self, scratch_db: tuple[str, Any]
    ) -> None:
        dbname, _conn = scratch_db
        result, _ = _wait(dbname, timeout=1)
        assert result.returncode == 1, result.stderr

    def test_an_unreachable_database_is_waited_for_and_named(self) -> None:
        cmd, env = _wait_cmd("schema_wait_test_never_created", timeout=1)
        result = subprocess.run(
            cmd, cwd=BACKEND, env=env, capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 1, result.stderr
        assert "unreachable" in result.stderr and "GIVING UP" in result.stderr
        password = make_url(os.environ["DATABASE_URL"]).password
        assert not password or password not in result.stderr

    def test_the_wait_ends_when_the_migration_lands(
        self, scratch_db: tuple[str, Any], head_and_previous: tuple[str, str]
    ) -> None:
        dbname, conn = scratch_db
        head, previous = head_and_previous
        _stamp(conn, previous)
        cmd, env = _wait_cmd(dbname, timeout=60)
        proc = subprocess.Popen(
            cmd, cwd=BACKEND, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        try:
            time.sleep(3)
            assert (
                proc.poll() is None
            ), f"stopped waiting on a stale schema: {proc.communicate()[0]}"
            _stamp(conn, head)
            landed = time.monotonic()
            out, _ = proc.communicate(timeout=30)
        finally:
            if proc.poll() is None:
                proc.kill()
        assert proc.returncode == 0, out
        assert "waiting for the schema" in out and time.monotonic() - landed < 25

    def test_a_database_ahead_of_the_code_starts_with_a_warning(
        self, scratch_db: tuple[str, Any]
    ) -> None:
        dbname, conn = scratch_db
        _stamp(conn, "ffffffffffff")
        result, _ = _wait(dbname, timeout=30)
        assert result.returncode == 0 and "AHEAD" in result.stderr, result.stderr
