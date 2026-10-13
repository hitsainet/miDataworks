"""Selector and grader configuration versions for miForge (FR-008.42; FTID 008 section 7.5). M3.

A body is ``{"plugin": <name>, "params": {...}}``; plugin names follow BRD-02 R-02.9 (``rule``,
``judge``, ``reference_match``, ``human_review``). The body is validated per plugin, stored in its
canonical form, and hashed with the one canonical-JSON function, so key order never changes the
hash. miForge copies ``body_sha256`` into ``selector_hash`` / ``grader_hash`` (BRD-02 R-02.10).
Versions are numbered per (kind, name) and are insert-only (database trigger, migration 0011).

The body format (``dw.selector-config/v1``, ``dw.grader-config/v1``) is miDataworks' proposal:
miForge has no code yet (checked 2026-10-07: ``ls /home/x-sean/app/miForge`` has no ``backend/``),
so its owner confirms it (008 FTASKS 12.1).
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...core.errors import AppError, NotFoundError
from ...core.ids import new_id
from ...models.publish import ConfigVersion
from ..identity import digest_text

BODY_FORMATS = {"selector": "dw.selector-config/v1", "grader": "dw.grader-config/v1"}

#: plugin -> (allowed params, required params)
PLUGINS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "rule": (
        frozenset({"regex", "min_length", "max_length", "required_terms", "forbidden_terms"}),
        frozenset(),
    ),
    "judge": (
        frozenset({"prompt_template", "model_id"}),
        frozenset({"prompt_template", "model_id"}),
    ),
    "reference_match": (
        frozenset({"reference_version_id", "match"}),
        frozenset({"reference_version_id", "match"}),
    ),
    "human_review": (frozenset({"queue"}), frozenset()),
}


class ConfigInvalid(AppError):
    status_code = 422
    code = "config_body_invalid"


def canonical_body(body: dict[str, Any]) -> dict[str, Any]:
    """Validate and return the canonical body (plugin + params, nothing else)."""
    if set(body) != {"plugin", "params"}:
        raise ConfigInvalid('A configuration body is exactly {"plugin": …, "params": {…}}.')
    plugin = body["plugin"]
    params = body["params"]
    if plugin not in PLUGINS or not isinstance(params, dict):
        raise ConfigInvalid(f"Unknown plugin {plugin!r}; one of {sorted(PLUGINS)}.")
    allowed, required = PLUGINS[plugin]
    unknown = sorted(set(params) - allowed)
    missing = sorted(required - set(params))
    if unknown or missing:
        raise ConfigInvalid(
            f"{plugin}: unknown params {unknown}, missing params {missing}.",
            details={"unknown": unknown, "missing": missing},
        )
    if plugin == "rule" and not params:
        raise ConfigInvalid("A rule names at least one condition.")
    if plugin == "reference_match" and params["match"] not in ("exact", "fuzzy"):
        raise ConfigInvalid("reference_match.match is exact or fuzzy.")
    canonical: dict[str, Any] = json.loads(json.dumps({"plugin": plugin, "params": params}))
    return canonical


def body_sha256(body: dict[str, Any]) -> str:
    return digest_text(canonical_body(body))


def create(
    session: Session,
    *,
    kind: str,
    name: str,
    body: dict[str, Any],
    parent_id: str | None,
    created_by: str,
    origin: str,
) -> ConfigVersion:
    canonical = canonical_body(body)
    if parent_id is not None:
        parent = session.get(ConfigVersion, parent_id)
        if parent is None or parent.kind != kind or parent.name != name:
            raise ConfigInvalid("parent_id names no earlier version of this configuration.")
    number = (
        session.execute(
            select(func.max(ConfigVersion.number)).where(
                ConfigVersion.kind == kind, ConfigVersion.name == name
            )
        ).scalar_one_or_none()
        or 0
    ) + 1
    row = ConfigVersion(
        id=new_id("cfg"),
        kind=kind,
        name=name,
        number=number,
        plugin=canonical["plugin"],
        body=canonical,
        body_sha256=digest_text(canonical),
        parent_id=parent_id,
        created_by=created_by,
        created_by_origin=origin,
    )
    session.add(row)
    session.commit()
    return row


def get(session: Session, config_id: str) -> ConfigVersion:
    row = session.get(ConfigVersion, config_id)
    if row is None:
        raise NotFoundError(f"No configuration version {config_id}.", code="config_not_found")
    return row


def list_versions(session: Session, kind: str | None = None) -> list[ConfigVersion]:
    query = select(ConfigVersion).order_by(
        ConfigVersion.kind, ConfigVersion.name, ConfigVersion.number
    )
    if kind:
        query = query.where(ConfigVersion.kind == kind)
    return list(session.execute(query).scalars())
