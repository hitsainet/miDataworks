"""One clock for the backend, so tests can move time without patching ``datetime``."""

from __future__ import annotations

import time
from datetime import UTC, datetime


def utc_now() -> datetime:
    """Timezone-aware UTC now. Every timestamp column is ``timestamptz`` (ADR-003)."""
    return datetime.now(UTC)


def monotonic() -> float:
    """Monotonic seconds, for throttles. Patched in tests as ``src.core.clock.monotonic``."""
    return time.monotonic()
