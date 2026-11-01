"""Rule sets travel with the data (FR-016.26, FR-016.27; FTID 016 section 3.8).

:func:`carried` reads which rule-set versions a version's build EXECUTED (``dw_rule_set_uses`` of the
step executions the version links, ``dw_version_steps``), each with its exact stored
``body_canonical`` text: the bytes the identity hashes and the bytes a published file holds
(ADR-018: never re-serialised). It answers ``None`` for a version built before rule sets were
recorded (migration ``0037``'s ``dw_versions.rule_sets_recorded`` is false and no use row exists),
which every reader shows as "rule sets not recorded", never as an empty list (FTID I12).

The four manifest writers (publish, draft card, export, reward bundle) call :func:`extensions_for`,
so the listing cannot drift between them; the three that write files beside their manifest
(publish build, export, reward bundle) call :func:`write_files`. The listing
(``extensions["midataworks.rule-sets/v1"]``) is validated against ``RuleSetListing`` (the
``$defs.RuleSetListing`` of ``midataworks-rule-set-v1.json``) before every write.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ...core.storage import atomic_write_bytes
from ...models.publish import ConfigVersion
from ...models.rule_set import RuleSetUse
from ...models.version import Version, VersionStep
from ...rules.rule_set import LISTING_FORMAT, RuleSetListing
from ..identity import bytes_sha256

DIRECTORY = "rule_sets"


@dataclass(frozen=True)
class CarriedRuleSet:
    name: str
    version: int
    sha256: str
    behaviour_key: str
    use: str
    body_canonical: str

    @property
    def path(self) -> str:
        return f"{DIRECTORY}/{self.name}@{self.version}.json"

    @property
    def content(self) -> bytes:
        return self.body_canonical.encode("utf-8")

    def listed(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "sha256": self.sha256,
            "behaviour_key": self.behaviour_key,
            "use": self.use,
            "path": self.path,
        }


def _query(version_id: str) -> Any:
    return (
        select(
            ConfigVersion.name,
            ConfigVersion.number,
            ConfigVersion.body_sha256,
            RuleSetUse.behaviour_key,
            RuleSetUse.use,
            ConfigVersion.body_canonical,
        )
        .join(RuleSetUse, RuleSetUse.config_version_id == ConfigVersion.id)
        .join(VersionStep, VersionStep.step_execution_id == RuleSetUse.step_execution_id)
        .where(VersionStep.version_id == version_id)
        .distinct()
    )


def _carried(rows: Sequence[Sequence[Any]], recorded: bool) -> list[CarriedRuleSet] | None:
    items = sorted(
        {
            CarriedRuleSet(str(r[0]), int(r[1]), str(r[2]), str(r[3]), str(r[4]), str(r[5]))
            for r in rows
        },
        key=lambda c: (c.name, c.version, c.use),
    )
    if not items and not recorded:
        return None
    return items


def carried(session: Session, version: Version) -> list[CarriedRuleSet] | None:
    """The rule-set versions the version's build ran, or None: "not recorded"."""
    rows = session.execute(_query(version.id)).all()
    return _carried(rows, bool(version.rule_sets_recorded))


async def carried_async(db: AsyncSession, version: Version) -> list[CarriedRuleSet] | None:
    rows = (await db.execute(_query(version.id))).all()
    return _carried(rows, bool(version.rule_sets_recorded))


def listing(items: Sequence[CarriedRuleSet] | None) -> dict[str, Any] | None:
    """``{"recorded": true, "rule_sets": [...]}`` validated against ``RuleSetListing``; None when
    not recorded (the manifest then has no key: absence means "not recorded")."""
    if items is None:
        return None
    value = {"recorded": True, "rule_sets": [c.listed() for c in items]}
    RuleSetListing.model_validate(value)
    return value


def read_block(items: Sequence[CarriedRuleSet] | None) -> dict[str, Any]:
    """What ``GET /versions/{id}`` shows: ``{recorded: false}`` or the listing."""
    found = listing(items)
    return found if found is not None else {"recorded": False, "rule_sets": None}


def extensions_for(
    lineage: Mapping[str, Any],
    items: Sequence[CarriedRuleSet] | None,
    breakdown_columns: Sequence[str] | None = None,
    *,
    code_rules: Sequence[Any] | None = None,
    behaviour_definition: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The manifest's ``extensions``: the lineage and, when recorded, the rule-set listing; 017
    (FR-017.51) adds ``breakdown_columns`` for a behaviour version (left out otherwise) and
    (FR-017.11, FTID 017 §10.1) the ``behaviour_definition`` the version was built against; 018
    (FR-018.40) adds the code-rule listing when recorded (``services/code_rules/carry``)."""
    out: dict[str, Any] = {"lineage": lineage}
    found = listing(items)
    if found is not None:
        out[LISTING_FORMAT] = found
    from ..code_rules import carry as code_rule_carry

    code_found = code_rule_carry.listing(code_rules)
    if code_found is not None:
        out[code_rule_carry.LISTING_FORMAT] = code_found
    if breakdown_columns is not None:
        out["breakdown_columns"] = list(breakdown_columns)
    if behaviour_definition:
        out["behaviour_definition"] = dict(behaviour_definition)
    return out


def write_files(items: Sequence[CarriedRuleSet] | None, root: Path) -> list[dict[str, Any]]:
    """Write each carried document, its exact stored bytes, at its listed path under ``root``;
    one file per rule-set version (two uses of one version share it)."""
    from ..publishing.digests import bytes_git_blob_sha1  # the publishing package imports models

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items or []:
        if item.path in seen:
            continue
        seen.add(item.path)
        content = item.content
        if bytes_sha256(content) != item.sha256:
            raise ValueError(f"{item.path}: stored bytes do not hash to the recorded sha256")
        atomic_write_bytes(root / item.path, content)
        out.append(
            {
                "name": item.name,
                "version": item.version,
                "sha256": item.sha256,
                "behaviour_key": item.behaviour_key,
                "path": item.path,
                "bytes": len(content),
                "git_blob_sha1": bytes_git_blob_sha1(content),
            }
        )
    return out
