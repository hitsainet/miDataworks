"""Poll loops re-read their interval at each poll (feature 012, FTID I6).

A detector-set send waits for a publish by polling. The interval was read once before the loop, so
a change could not reach a send already waiting. Here the overrides change between two polls (the
refresh is stubbed to apply them) and the second sleep must use the new interval. Control: read the
interval once before the loop again -> both sleeps use the environment's 10 s.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.core import runtime_settings
from src.workers import detector_send_tasks


class _Db:
    def __init__(self, statuses: list[str]) -> None:
        self.statuses = statuses

    def get(self, *_: Any, **__: Any) -> Any:
        return SimpleNamespace(status=self.statuses.pop(0))


def test_a_waiting_send_picks_up_a_new_poll_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    refreshes: list[int] = []

    def refresh(*_: Any, **__: Any) -> bool:
        refreshes.append(1)
        if len(refreshes) == 2:  # another process changed it while the send waited
            runtime_settings.apply({"detector_send_poll_seconds": "2"})
        return True

    monkeypatch.setattr(runtime_settings, "refresh_if_stale", refresh)
    monkeypatch.setattr(detector_send_tasks, "SLEEP", sleeps.append)
    monkeypatch.setattr(detector_send_tasks, "record_progress", lambda *a, **k: None)
    checker = SimpleNamespace(raise_if_cancelled=lambda *_: None)
    db = _Db(["uploading", "uploading", "published"])
    pub = detector_send_tasks._wait_for_publish(db, "job_1", "pub_1", checker)  # type: ignore[arg-type]
    assert pub.status == "published"
    assert sleeps == [10.0, 2.0]
