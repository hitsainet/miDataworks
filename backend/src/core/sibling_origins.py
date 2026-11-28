"""Previous-address equivalence for the two siblings (feature 020, part C; FTID §3.4, FTDD §6.3).

At each cutover (ENH-009 P2, P3) a sibling's in-cluster address changes while the INSTANCE stays the
same. Reward marks, registrations, reproduction links and detector sends are looked up by the
address they were made with, and paused runs refuse ``ENDPOINT_CHANGED`` when the address moved.
Rewriting the stored values is ruled out (R1), so each sibling keeps a restart-only list of the
origins at which the same instance answered before (``MISERVER_PREVIOUS_BASE_URLS``,
``MIWORKBENCH_PREVIOUS_BASE_URLS``), and this module is the only place two sibling addresses are
compared (``tests/unit/test_sibling_origins_wiring.py`` holds the call sites to it).

What it guarantees:
- **Opt-in.** With both lists empty every function answers exactly as the ``==`` it replaced: two
  addresses are the same sibling only when their text is equal (``same_sibling``'s second clause
  needs a LISTED previous origin; control M54).
- **Read at call time** through ``get_settings()``, so a runtime override of the current address
  reaches every lookup at once (012 FTID I1).
- **Origins only.** A stored value with a path matches by its exact text only; a call is rewritten
  only from a listed previous origin to the configured current address, and the log line names
  the two origins, never a key or a path (TPD-8: a key bound to the old origin is never sent to the
  new one; ``key_binding.key_for`` is untouched).
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import func, select

from .config import get_settings
from .key_binding import url_origin

logger = logging.getLogger(__name__)


class Sibling(StrEnum):
    """A sibling and the settings that hold its current and previous addresses."""

    MISERVER = "miserver"  # current: millm_base_url; previous: miserver_previous_base_urls
    MIWORKBENCH = "miworkbench"  # current: mistudio_base_url; previous: miworkbench_previous_...


def _current(s: Sibling) -> str | None:
    settings = get_settings()
    return settings.millm_base_url if s is Sibling.MISERVER else settings.mistudio_base_url


def _previous(s: Sibling) -> tuple[str, ...]:
    settings = get_settings()
    if s is Sibling.MISERVER:
        return tuple(settings.miserver_previous_base_urls)
    return tuple(settings.miworkbench_previous_base_urls)


def group(s: Sibling) -> frozenset[str]:
    """Every origin at which ``s`` answers now or answered before: current plus previous."""
    origins = {url_origin(_current(s)), *(url_origin(p) for p in _previous(s))}
    return frozenset(o for o in origins if o is not None)


def _is_origin_only(url: str) -> bool:
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    return parts.path in ("", "/") and not parts.query and not parts.fragment


def _path_of(url: str) -> tuple[str, str, str] | None:
    """Path (without a trailing ``/``), query and fragment: what must agree beside the origin."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    return parts.path.rstrip("/"), parts.query, parts.fragment


def same_sibling(s: Sibling, a: str | None, b: str | None) -> bool:
    """True when ``a`` and ``b`` address the same instance of ``s``.

    Today's exact rule first (equal text). Otherwise the two must carry the SAME path (an address
    with a path, such as ``…/staging``, is never the instance at a bare origin; an endpoint role's
    ``/v1`` on both sides is), both origins must be in ``s``'s group, AND at least one of them a
    LISTED previous origin, so an empty list behaves exactly as ``==`` (review round 2).
    """
    if a == b:
        return True
    if a is None or b is None or _path_of(a) != _path_of(b):
        return False
    origin_a, origin_b = url_origin(a), url_origin(b)
    if origin_a is None or origin_b is None:
        return False
    members = group(s)
    if origin_a not in members or origin_b not in members:
        return False
    previous = {url_origin(p) for p in _previous(s)}
    return origin_a in previous or origin_b in previous


def matching_values(session: Any, column: Any, s: Sibling, url: str) -> list[str]:
    """The stored values of ``column`` that address the same instance of ``s`` as ``url``.

    ``url`` outside ``s``'s group, with a path, or no previous origin listed → ``[url]`` and no
    query, so the caller's ``IN`` is exactly today's ``==`` (opt-in). Otherwise one loose index
    scan of ``column`` (``distinct_values_statement``), keeping each stored value
    whose origin is in the group and whose path is empty or ``/``, plus ``url`` itself. Sorted, so
    the SQL a caller builds from it is deterministic.
    """
    members = group(s)
    if url_origin(url) not in members or not _is_origin_only(url):
        return [url]
    if not _previous(s):
        return [url]
    stored = session.scalars(distinct_values_statement(column))
    values = {url}
    for value in stored:
        if value and _is_origin_only(value) and url_origin(value) in members:
            values.add(value)
    return sorted(values)


def call_base(s: Sibling, stored: str) -> str:
    """The address to CALL for a row stored with ``stored``.

    A listed previous origin of ``s`` with a current address configured → the current address
    (logged with both origins, nothing else). Anything else → ``stored``, unchanged.
    """
    current = _current(s)
    origin = url_origin(stored)
    previous = {url_origin(p) for p in _previous(s)}
    if current and origin is not None and origin in previous and origin != url_origin(current):
        logger.info(
            "code=sibling_previous_origin_rewrite sibling=%s from_origin=%s to_origin=%s",
            s.value,
            origin,
            url_origin(current),
        )
        return current
    return stored


def previous_sibling_of(url: str | None) -> Sibling | None:
    """The sibling whose PREVIOUS list holds ``url``'s origin, else None."""
    origin = url_origin(url)
    if origin is None:
        return None
    for s in Sibling:
        if origin in {url_origin(p) for p in _previous(s)}:
            return s
    return None


def current_address_for(role_base_url: str, s: Sibling) -> str | None:
    """ID-8: the role's address re-pointed at ``s``'s current origin, keeping its own path and query
    (``http://old:8000/v1`` → ``http://new:8000/v1``). None unless the role is on a previous origin
    of ``s`` and a current address is configured."""
    current = _current(s)
    if not current or previous_sibling_of(role_base_url) is not s:
        return None
    target = urlsplit(current.strip())
    own = urlsplit(role_base_url.strip())
    return urlunsplit((target.scheme, target.netloc, own.path, own.query, own.fragment))


def prefer_exact(column: Any, url: str) -> Any:
    """An ORDER BY term putting the row stored with exactly ``url`` first (ID-7): after ``IN`` over
    several origins two rows can match (one made before a cutover, one after), and the row of the
    current address must win without ``MultipleResultsFound``."""
    return (column == url).desc()


def distinct_values_statement(column: Any) -> Any:
    """Every distinct stored value of ``column``, by a LOOSE INDEX SCAN (020 FTASKS 6.15).

    ``SELECT DISTINCT`` reads every row (12-15 ms at 100,000 rows, over the 5 ms budget). This
    recursive CTE asks the index that leads with ``column`` for ``min(column) > previous`` once per
    distinct value, so the cost follows the number of addresses (a handful), not the table size.
    Each table has such an index: ``uq_dw_reward_marks_probe``, ``uq_dw_reproduction_links_target``
    and ``ix_dw_mistudio_registrations_base_url`` (migration 0048). The column must be NOT NULL.
    ``tests/integration/test_matching_values_plan.py`` reads the plan's row counts."""
    step = select(func.min(column).label("value")).cte("sibling_addresses", recursive=True)
    following = (
        select(func.min(column)).where(column > step.c.value).scalar_subquery().label("value")
    )
    step = step.union_all(select(following).where(step.c.value.is_not(None)))
    return select(step.c.value).where(step.c.value.is_not(None))
