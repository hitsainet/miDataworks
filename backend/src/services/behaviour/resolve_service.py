"""Resolve a behaviour key against miStudio's catalogue (017 FR-017.10, FR-017.11; FTID §7.8).

One read of ``GET /api/v1/probe-monitors/behaviours/{key}`` (miStudio 037, detected at run time:
ADR-027), no cache. Three answers, never a guess:

- ``verified``: the catalogue served the key; the answer carries its current definition (version,
  both meanings, target FPR), ``current_version`` and ``archived``;
- ``not in the catalogue``: miStudio's catalogue answered ``404 not_found`` for the key (any other
  ``404`` is "not verified");
- ``not verified against a catalogue``: miStudio is not configured (miDataworks works without it),
  did not answer, refused, or answered something that is not this key's head.

``record`` is the definition in the shape a build records (``dw_versions.behaviour_definition``,
TD-017.32): an agent passes it into ``carrier_plan@1``'s ``definition`` parameter, so the build's
request digest binds what was resolved; operators never call the network. When the read is not
verified, ``record`` still names the key and says ``verified: false``; the agent supplies the
meaning itself.
"""

from __future__ import annotations

from typing import Any

from ...clients import mistudio_client as mc
from ...core.clock import utc_now
from ...core.config import get_settings

NOT_VERIFIED = "not verified against a catalogue"
NOT_IN_CATALOGUE = "not in the catalogue"


def _now() -> str:
    return utc_now().isoformat()


def _unverified(key: str, reason: str, detail: str) -> dict[str, Any]:
    return {
        "key": key,
        "verified": False,
        "reason": reason,
        "detail": detail,
        "definition": None,
        "current_version": None,
        "archived": None,
        "record": record(key, None, None, verified=False),
    }


def record(
    key: str,
    definition_version: int | None,
    positive_meaning: str | None,
    *,
    verified: bool,
    read_at: str | None = None,
) -> dict[str, Any]:
    """The recorded definition (TD-017.32): ``source`` is ``catalogue`` only when verified."""
    return {
        "key": key,
        "definition_version": definition_version,
        "positive_meaning": positive_meaning,
        "verified": verified,
        "source": "catalogue" if verified else "supplied",
        "read_at": read_at or _now(),
    }


def resolve(key: str) -> dict[str, Any]:
    """The catalogue's answer for ``key`` (blocking; routes call it from a thread)."""
    base = get_settings().mistudio_base_url
    if not base:
        return _unverified(
            key,
            NOT_VERIFIED,
            "miStudio is not configured (MISTUDIO_BASE_URL), so the key was not checked; "
            "supply the definition yourself.",
        )
    try:
        with mc.MiStudioClient(base) as client:
            head = client.get_behaviour(key)
    except mc.MiStudioError as exc:
        return _unverified(key, NOT_VERIFIED, f"miStudio's catalogue was not read: {exc.message}")
    if head is None:
        return _unverified(
            key,
            NOT_IN_CATALOGUE,
            f"miStudio's catalogue has no behaviour {key!r}; catalogue it there, or supply the "
            "definition yourself.",
        )
    definition = head.get("definition") if isinstance(head, dict) else None
    if not isinstance(definition, dict) or head.get("key") != key:
        return _unverified(
            key,
            NOT_VERIFIED,
            "miStudio answered the catalogue read without this key's definition.",
        )
    version = definition.get("version")
    read_at = _now()
    return {
        "key": key,
        "verified": True,
        "reason": "verified against miStudio's catalogue",
        "detail": None,
        "definition": {
            "version": version,
            "positive_meaning": definition.get("positive_meaning"),
            "negative_meaning": definition.get("negative_meaning"),
            "target_fpr": definition.get("target_fpr"),
        },
        "current_version": head.get("current_version"),
        "archived": bool(head.get("archived")),
        "record": record(
            key,
            int(version) if isinstance(version, int) else None,
            definition.get("positive_meaning"),
            verified=True,
            read_at=read_at,
        ),
    }
