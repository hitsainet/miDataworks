"""The old-name block of ``GET /api/health`` (feature 020; FR-020.5, FR-020.6; BR-023; CF-4, CF-24;
FTASKS 4.9, 4.12).

``legacy_names`` carries EXACTLY BR-023's four keys in every case (clean, names in use, an
unreadable table): S001's sampler and the three sibling apps read the same shape. Everything else
is in ``legacy_names_meta``, whose ``error`` key is always present (``null`` when clean): a missing
key would read as UNMEASURED, and a dropped error would read as "nothing in use".
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.core import env_names, legacy_names
from src.core.config import get_settings
from src.core.database import get_sync_engine
from tests.support.env_rows import in_transition

FOUR = {"in_use", "also_set", "conflicts", "retired_but_set"}
META = {"error", "observing_since", "in_use_window_hours", "watching"}
#: Addresses that refuse at once (no DNS, no wait): health probes the siblings.
OLD = "http://127.0.0.1:9"
NEW = "http://127.0.0.1:7"


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, clean_db: None) -> Iterator[pytest.MonkeyPatch]:
    in_transition(monkeypatch)  # the rows are retired since P7; these tests drive the evidence
    for row in env_names.RENAMES:
        monkeypatch.delenv(row.new, raising=False)
        monkeypatch.delenv(row.old, raising=False)
    legacy_names.reset()
    get_settings.cache_clear()  # type: ignore[attr-defined]
    yield monkeypatch
    legacy_names.reset()
    get_settings.cache_clear()  # type: ignore[attr-defined]


def _reload() -> None:
    get_settings.cache_clear()  # type: ignore[attr-defined]
    get_settings()


async def _health(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.get("/api/health")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _insert(old: str, new: str, last_seen: datetime) -> None:
    with get_sync_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO dw_legacy_name_uses (kind, old_name, new_name, first_seen_at, "
                "last_seen_at, count) VALUES ('env', :o, :n, :t, :t, 1)"
            ),
            {"o": old, "n": new, "t": last_seen},
        )


async def test_legacy_names_has_exactly_four_keys(
    env: pytest.MonkeyPatch, client: httpx.AsyncClient
) -> None:
    clean = await _health(client)
    assert set(clean["legacy_names"]) == FOUR
    assert clean["legacy_names"] == {
        "in_use": [],
        "also_set": [],
        "conflicts": [],
        "retired_but_set": [],
    }
    env.setenv("MILLM_BASE_URL", OLD)
    env.setenv("MISTUDIO_BASE_URL", OLD)
    env.setenv("MIWORKBENCH_BASE_URL", NEW)
    _reload()
    busy = await _health(client)
    assert set(busy["legacy_names"]) == FOUR
    assert "error" not in busy["legacy_names"]
    assert busy["legacy_names"]["also_set"] == [
        {"old": "MISTUDIO_BASE_URL", "new": "MIWORKBENCH_BASE_URL"}
    ]
    assert busy["legacy_names"]["conflicts"] == busy["legacy_names"]["also_set"]


async def test_entry_keys_are_exact(env: pytest.MonkeyPatch, client: httpx.AsyncClient) -> None:
    env.setenv("MILLM_UI_URL", "http://127.0.0.1:5")
    _reload()
    [entry] = (await _health(client))["legacy_names"]["in_use"]
    assert set(entry) == {"kind", "old", "new", "last_seen"}
    assert (entry["kind"], entry["old"], entry["new"]) == ("env", "MILLM_UI_URL", "MISERVER_UI_URL")
    assert entry["last_seen"].endswith("Z")


async def test_meta_keys_are_exact_and_always_present(
    env: pytest.MonkeyPatch, client: httpx.AsyncClient
) -> None:
    response = await client.get("/api/health")
    assert '"error":null' in response.text.replace(" ", ""), "error is serialised, never dropped"
    meta = response.json()["legacy_names_meta"]
    assert set(meta) == META
    assert meta["error"] is None
    assert meta["in_use_window_hours"] == 24
    assert meta["watching"] == {"env": 7}
    assert meta["observing_since"].endswith("Z")


async def test_window_is_24_hours(env: pytest.MonkeyPatch, client: httpx.AsyncClient) -> None:
    now = datetime.now(UTC)
    _insert("MILLM_LEASE_HOLDER", "MISERVER_LEASE_HOLDER", now - timedelta(hours=23))
    _insert("MISTUDIO_TIMEOUT_SECONDS", "MIWORKBENCH_TIMEOUT_SECONDS", now - timedelta(hours=25))
    body = await _health(client)
    assert [e["old"] for e in body["legacy_names"]["in_use"]] == ["MILLM_LEASE_HOLDER"]
    with get_sync_engine().connect() as conn:
        kept = conn.execute(text("SELECT old_name FROM dw_legacy_name_uses")).scalars().all()
    assert set(kept) == {"MILLM_LEASE_HOLDER", "MISTUDIO_TIMEOUT_SECONDS"}, "the record stays"
    oldest = (now - timedelta(hours=25)).strftime("%Y-%m-%dT%H:%M:%S")
    assert body["legacy_names_meta"]["observing_since"].startswith(oldest)


async def test_a_name_set_now_is_in_use(env: pytest.MonkeyPatch, client: httpx.AsyncClient) -> None:
    env.setenv("MILLM_BASE_URL", OLD)
    _reload()
    in_use = (await _health(client))["legacy_names"]["in_use"]
    assert [e["old"] for e in in_use] == ["MILLM_BASE_URL"]


async def test_a_retired_name_set_alone_is_reported_and_not_configured(
    monkeypatch: pytest.MonkeyPatch, clean_db: None, client: httpx.AsyncClient
) -> None:
    """P7 (FR-020.26, PD-12), through the real route and the REAL rows (no ``in_transition``): the
    old name alone is listed in ``retired_but_set``, is not "in use", and leaves miServer not
    configured; the sampler's ``watching`` counts no old name left to watch."""
    for row in env_names.RENAMES:
        monkeypatch.delenv(row.new, raising=False)
        monkeypatch.delenv(row.old, raising=False)
    monkeypatch.setenv("MILLM_BASE_URL", OLD)
    legacy_names.reset()
    _reload()
    try:
        body = await _health(client)
    finally:
        legacy_names.reset()
        get_settings.cache_clear()  # type: ignore[attr-defined]
    retired = [row.old for row in env_names.RENAMES if row.retired]
    if "MILLM_BASE_URL" not in retired:
        pytest.fail("MILLM_BASE_URL is not retired: this is P7's test (FR-020.26)")
    assert body["legacy_names"]["retired_but_set"] == ["MILLM_BASE_URL"]
    assert body["legacy_names"]["in_use"] == []
    assert body["dependencies"]["millm"]["configured"] is False
    assert body["dependencies"]["millm"]["address_source"] == "unset"
    assert body["legacy_names_meta"]["watching"] == {"env": len(env_names.RENAMES) - len(retired)}


async def test_unreadable_table_reports_meta_error_and_stays_200(
    env: pytest.MonkeyPatch, client: httpx.AsyncClient
) -> None:
    env.setenv("MILLM_BASE_URL", OLD)
    _reload()
    with get_sync_engine().begin() as conn:
        conn.execute(text("ALTER TABLE dw_legacy_name_uses RENAME TO dw_legacy_name_uses_away"))
    try:
        body = await _health(client)
    finally:
        with get_sync_engine().begin() as conn:
            conn.execute(text("ALTER TABLE dw_legacy_name_uses_away RENAME TO dw_legacy_name_uses"))
    assert set(body["legacy_names"]) == FOUR
    assert body["legacy_names_meta"]["error"].endswith(": cannot read dw_legacy_name_uses")
    assert [e["old"] for e in body["legacy_names"]["in_use"]] == ["MILLM_BASE_URL"]


async def test_health_call_touches(env: pytest.MonkeyPatch, client: httpx.AsyncClient) -> None:
    """T2: a health call records this process's old names (control M8c)."""
    env.setenv("MISTUDIO_BASE_URL", OLD)
    _reload()
    await _health(client)
    with get_sync_engine().connect() as conn:
        rows = conn.execute(text("SELECT old_name, count FROM dw_legacy_name_uses")).all()
    assert [tuple(r) for r in rows] == [("MISTUDIO_BASE_URL", 1)]


async def test_names_never_values_including_encoded_forms(
    env: pytest.MonkeyPatch, client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    """Values never leave: sentinels in every renamed variable are searched in the health blocks,
    both tables and every captured log record, raw, JSON-escaped and base64 (suite memory
    ``leak-tests-must-look-for-the-encoded-form``). The address fields' own ``url`` under
    ``dependencies`` is pre-existing reporting and is not part of this check."""
    caplog.set_level(logging.DEBUG)
    sentinels = {
        "MILLM_BASE_URL": "http://sentinel-020-one.invalid:9",
        "MILLM_UI_URL": "http://sentinel-020-two.invalid:9",
        "MILLM_LEASE_HOLDER": "SENTINEL-020-three",
        "MISTUDIO_BASE_URL": "http://sentinel-020-four.invalid:9",
        "MIWORKBENCH_BASE_URL": "http://sentinel-020-five.invalid:9",
    }
    for name, value in sentinels.items():
        env.setenv(name, value)
    _reload()
    body = await _health(client)
    with get_sync_engine().connect() as conn:
        tables = [
            [dict(r) for r in conn.execute(text(f"SELECT * FROM {table}")).mappings()]
            for table in ("dw_legacy_name_uses", "dw_legacy_name_write_errors")
        ]
    assert tables[0], "the touch wrote rows: the search is not over nothing"
    haystacks = [
        json.dumps({"names": body["legacy_names"], "meta": body["legacy_names_meta"]}),
        json.dumps(tables, default=str),
        "\n".join(r.getMessage() for r in caplog.records),
    ]
    for value in sentinels.values():
        forms = {
            value,
            json.dumps(value)[1:-1],
            base64.b64encode(value.encode()).decode(),
            base64.b64encode(value.encode()).decode().rstrip("="),
        }
        for haystack in haystacks:
            for form in forms:
                assert form not in haystack, f"{form!r} leaked"


async def test_a_hung_touch_never_holds_up_health(
    env: pytest.MonkeyPatch, client: httpx.AsyncClient
) -> None:
    """Review 2026-11-08: the T2 touch runs under the health probe bound. A touch that hangs is
    left behind and health answers (timing-independent: the touch is still blocked when health
    has answered; control M73)."""
    import threading

    release = threading.Event()
    entered = threading.Event()
    finished = threading.Event()

    def hung(**_: object) -> bool:
        entered.set()
        release.wait(30)
        finished.set()
        return False

    env.setattr(legacy_names, "touch_sync", hung)
    env.setattr(get_settings(), "health_probe_timeout_seconds", 0.2)
    try:
        response = await client.get("/api/health")
        assert response.status_code == 200
        assert entered.is_set() and not finished.is_set(), "health waited for the hung touch"
    finally:
        release.set()


async def test_health_answers_while_the_store_is_locked(
    env: pytest.MonkeyPatch, client: httpx.AsyncClient
) -> None:
    """Review round 2: the REAL touch (no stub) behind a lock another session holds. Health
    answers 200 with the block's error set while the lock is still held: the touch and the read
    are each left behind at the probe bound."""
    env.setenv("MILLM_BASE_URL", OLD)
    _reload()
    env.setattr(get_settings(), "health_probe_timeout_seconds", 0.5)
    env.setattr(legacy_names, "_engine", None)
    holder = get_sync_engine().connect()
    tx = holder.begin()
    try:
        holder.execute(text("LOCK TABLE dw_legacy_name_uses IN ACCESS EXCLUSIVE MODE"))
        response = await client.get("/api/health")
        still_held = holder.execute(
            text("SELECT count(*) FROM pg_locks WHERE mode = 'AccessExclusiveLock' AND granted")
        ).scalar()
    finally:
        tx.rollback()
        holder.close()
    assert response.status_code == 200, response.text
    assert still_held, "the lock was released before health answered"
    assert response.json()["legacy_names_meta"]["error"], "a locked store read as clean"


async def test_the_touch_and_the_read_share_one_budget(
    env: pytest.MonkeyPatch, client: httpx.AsyncClient
) -> None:
    """Review round 2: the touch and the read each took up to the probe bound T, so a locked store
    held health for 2T. They now share ONE deadline: the read is given what the touch left.
    Timing-free: the budget's clock and the bounded waits are injected and recorded (control M80).
    """
    from src.services import health_service

    bound = get_settings().health_probe_timeout_seconds
    ticks = iter([100.0, 100.0 + 0.75 * bound])
    env.setattr(health_service, "_monotonic", lambda: next(ticks))
    given: list[float] = []
    real_within = health_service._within

    async def within(awaitable: object, seconds: float) -> object:
        given.append(seconds)
        return await real_within(awaitable, seconds)

    env.setattr(health_service, "_within", within)
    env.setattr(legacy_names, "touch_sync", lambda **_: False)
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert given == [pytest.approx(bound), pytest.approx(0.25 * bound)], given
    assert response.json()["legacy_names_meta"]["error"] is None


async def test_a_touch_that_used_the_budget_skips_the_read_and_says_so(
    env: pytest.MonkeyPatch, client: httpx.AsyncClient
) -> None:
    """Review round 2: when the touch used the whole bound, the table is not read at all (no second
    wait) and the block reports why; health still answers 200 (control M80b)."""
    from src.services import health_service

    calls: list[str] = []

    async def within(awaitable: object, seconds: float) -> object:
        calls.append("wait")
        if hasattr(awaitable, "close"):
            awaitable.close()
        raise TimeoutError

    async def never_read(db: object) -> object:
        calls.append("read")
        raise AssertionError("the table was read after the touch used the budget")

    env.setattr(health_service, "_within", within)
    env.setattr(legacy_names, "block", never_read)
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert calls == ["wait"], calls
    error = response.json()["legacy_names_meta"]["error"]
    assert error.startswith("TimeoutError") and "touch" in error, error


async def test_a_touch_that_left_no_time_skips_the_read(
    env: pytest.MonkeyPatch, client: httpx.AsyncClient
) -> None:
    """Review round 3 (survivor H02): the touch returned exactly at the deadline. The read is not
    started with a zero budget (one bounded wait only), and the block says the touch left no time.
    """
    from src.services import health_service

    bound = get_settings().health_probe_timeout_seconds
    ticks = iter([100.0, 100.0 + bound])
    env.setattr(health_service, "_monotonic", lambda: next(ticks))
    given: list[float] = []
    real_within = health_service._within

    async def within(awaitable: object, seconds: float) -> object:
        given.append(seconds)
        return await real_within(awaitable, seconds)

    env.setattr(health_service, "_within", within)
    env.setattr(legacy_names, "touch_sync", lambda **_: False)
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert given == [pytest.approx(bound)], given
    error = response.json()["legacy_names_meta"]["error"]
    assert error and "left no time" in error, error
