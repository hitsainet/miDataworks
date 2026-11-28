"""Every API answer that carries a legacy ``next`` also carries ``next_steps`` (022 FR-022.8).

Installed on the shared backend test client (``tests/conftest.py`` ``client``) as an httpx response
hook, so EVERY JSON answer the API tests produce is checked: a top-level ``next`` or an
``error.details.next`` must have ``next_steps`` beside it, equal to ``next`` as a list, each item a
valid ``next_step`` of the vendored contract, with ``next`` itself as the backend built it. This is
what finds a success site a grep missed: the test that produced the answer fails, naming the route.

``INSPECTED`` counts the bodies with ``next`` the hook saw; ``tests/unit/test_next_steps_guard.py``
drives one through the shared client and asserts the count moved, so a hook that is not wired
cannot pass silently.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

INSPECTED: list[str] = []


def expected_steps(value: Any) -> list[Any]:
    """``next`` as the contract's list: each item that names a tool, as ``{tool, args, why}``."""
    items = [] if value is None else (value if isinstance(value, list) else [value])
    steps = [
        {
            "tool": i["tool"],
            "args": i.get("args") or {},
            "why": (str(i.get("why") or "").strip() or f"call {i['tool']}")[:300],
        }
        for i in items
        if isinstance(i, dict) and isinstance(i.get("tool"), str)
    ]
    return steps[:5]


def _mappings(body: Any, label: str = "body") -> list[tuple[str, dict[str, Any]]]:
    """Every dict reachable from ``body`` through dict values (review R2: a ``next`` nested in a
    result, such as the operator preview's, needs its ``next_steps`` too)."""
    if not isinstance(body, dict):
        return []
    found = [(label, body)]
    for key, value in body.items():
        # A nested ``details`` is a RECORDED refusal (a check's first refusal, a job's stored
        # error), carried verbatim; the API's own refusal is ``error.details``, set by envelope().
        if key == "details" and label != "body.error":
            continue
        found += _mappings(value, f"{label}.{key}")
    return found


def check_body(body: Any, where: str) -> int:
    """Raise ``AssertionError`` when a ``next`` lacks its ``next_steps``. Returns how many it saw."""
    from tests.support.result_language import validate

    seen = 0
    for label, mapping in _mappings(body):
        if "next_steps" in mapping:
            # Review R3: a next_steps is the contract's list wherever it appears, next or not.
            validate("result_fields", {"next_steps": mapping["next_steps"]})
        if "next" not in mapping:
            continue
        seen += 1
        assert (
            "next_steps" in mapping
        ), f"{where}: {label} has next and no next_steps (022 FR-022.8)"
        assert mapping["next_steps"] == expected_steps(mapping["next"]), (where, label)
        validate("result_fields", {"next_steps": mapping["next_steps"]})
    return seen


async def response_hook(response: httpx.Response) -> None:
    if "json" not in response.headers.get("content-type", ""):
        return
    await response.aread()
    try:
        body = json.loads(response.content)
    except ValueError:
        return
    where = f"{response.request.method} {response.request.url.path} -> {response.status_code}"
    if check_body(body, where):
        INSPECTED.append(where)
