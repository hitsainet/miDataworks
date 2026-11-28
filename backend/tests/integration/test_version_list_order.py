"""``GET /versions?order=recent`` (novice walk 2026-10-19, finding 5).

The Label step listed versions by dataset NAME, 200 at a time, so a dataset made a minute ago whose
name sorted late was missing behind a hundred old test versions. ``order=recent`` lists the newest
version first across datasets; the default stays ``name`` for every existing caller.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from src.core.database import sync_session_factory
from tests.support import db_factories


def _seed() -> tuple[str, str]:
    now = datetime.now(UTC)
    with sync_session_factory()() as db:
        old = db_factories.version(
            db, db_factories.dataset(db, name="aaa-old-test"), created_at=now - timedelta(days=9)
        )
        new = db_factories.version(
            db, db_factories.dataset(db, name="zzz-novice-humor"), created_at=now
        )
        db.commit()
        return str(old.id), str(new.id)


async def test_recent_lists_the_newest_version_first(
    client: httpx.AsyncClient, migrated_database: None
) -> None:
    old, new = _seed()
    recent = (await client.get("/api/v1/versions", params={"order": "recent"})).json()["items"]
    by_name = (await client.get("/api/v1/versions")).json()["items"]
    assert [v["id"] for v in recent][:2] == [new, old]
    ids = [v["id"] for v in by_name]
    assert ids.index(old) < ids.index(new)  # the default is unchanged: by dataset name


async def test_an_unknown_order_is_refused(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/versions", params={"order": "random"})).status_code == 422
