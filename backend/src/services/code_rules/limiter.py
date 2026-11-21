"""The deployment-wide dry-run rate limit (FR-018.27; FTDD §7.4; FTID §3.3, I11).

A Redis fixed window: one counter per minute (``dw:code_rules:dry_run:<epoch minute>``),
``INCR`` + ``EXPIRE 120`` in one pipeline. Over ``CODE_RULE_DRY_RUN_PER_MINUTE`` the call is
refused 429 naming the setting. Redis unreachable fails CLOSED: 503 ``rate_limiter_unavailable``
(a limiter that waves calls through while it is down is no limit).
"""

from __future__ import annotations

import time

import redis.asyncio as aioredis

from ...core.config import get_settings
from .errors import CodeRuleError

KEY = "dw:code_rules:dry_run:{minute}"
WINDOW_TTL_S = 120


async def take() -> int:
    """Count this call in the current minute; refuse past the limit. Returns the count."""
    settings = get_settings()
    limit = settings.code_rule_dry_run_per_minute
    key = KEY.format(minute=int(time.time() // 60))
    client = aioredis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
    try:
        async with client.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, WINDOW_TTL_S)
            count, _ = await pipe.execute()
    except Exception as exc:  # noqa: BLE001 - any Redis failure refuses (fail closed)
        raise CodeRuleError(
            503,
            "rate_limiter_unavailable",
            f"The dry-run rate limiter cannot reach Redis ({type(exc).__name__}); nothing was run.",
            {},
            next_tool="dataworks_health",
            why="see whether Redis is up",
        ) from None
    finally:
        await client.aclose()
    if int(count) > limit:
        raise CodeRuleError(
            429,
            "rate_limited",
            f"CODE_RULE_DRY_RUN_PER_MINUTE allows {limit} dry runs a minute in this deployment; "
            "wait for the next minute.",
            {"setting": "CODE_RULE_DRY_RUN_PER_MINUTE", "limit": limit},
            next_tool="dataworks_get_runner_contract",
            why="read the dry-run limit in force",
        )
    return int(count)
