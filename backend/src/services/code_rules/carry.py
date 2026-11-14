"""Code rules travel with the data (feature 018 slice 4; FR-018.40 – FR-018.42; FTDD §5.7, §7.8).

:func:`carried` reads which code rules a version's build EXECUTED (``dw_code_rule_uses`` of the step
executions the version links, ``dw_version_steps``), each with its exact stored canonical document
(the bytes ``code_sha256`` hashes) and its cases. It answers ``None`` for a version built before
code rules were recorded (``0044``'s ``dw_versions.code_rules_recorded`` false and no use row): every
reader shows "code rules not recorded", never an empty list.

**Two files per carried code rule** (FTDD §5.7): ``code_rules/<sha256>.json`` holds EXACTLY the
canonical bytes that were hashed (a consumer re-hashes it and compares the listed ``code_sha256``),
and ``code_rules/<sha256>.cases.json`` holds the cases (canonical JSON, its own sha256): cases are
outside the hash, so one file cannot be both.

**The listing** ``extensions["midataworks.code-rules/v1"]`` is validated against
``CodeRuleListing`` (``$defs.CodeRuleListing`` of ``midataworks-runner-v1.json``) before every write,
through 016's ONE helper ``rules.carry.extensions_for(..., code_rules=...)``, so the four manifest
writers (publish, draft card, export, reward bundle) cannot drift.

**Visibility floor** (FR-018.41, FTDD F5 (a)): the strictest visibility among the sources the rule was
derived from (``derived_from.sources``) and the source of every case
(``source.source_version_id``): ``forbids_redistribution`` (a source whose licence forbids it) over
``private_only`` (a version declaring that floor, or a reference that resolves to nothing, failing
closed) over ``none``. Builds that merely RUN the rule never lower it; check C-9 refuses a public push
carrying a rule whose floor is not ``none``, and C-8 reads its cases with the public-value rule.

**Current approval (FR-018.42)**: ``GET /versions/{id}`` shows each carried rule's approval state NOW
("built with a since-revoked code rule"); the files and hashes never change.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ...core.canonical_json import canonical_json
from ...core.storage import atomic_write_bytes
from ...models.code_rule import CodeRule, CodeRuleUse
from ...models.publish import ConfigVersion
from ...models.version import Version, VersionStep
from ...runner_contract.document import CodeRuleListing
from ..identity import bytes_sha256

DIRECTORY = "code_rules"
LISTING_FORMAT = "midataworks.code-rules/v1"
#: Visibility floors, weakest first.
FLOORS = ("none", "private_only", "forbids_redistribution")


@dataclass(frozen=True)
class CarriedCodeRule:
    name: str
    version: int
    behaviour_key: str
    code_sha256: str
    runner_contract: str
    canonical: str
    cases: tuple[dict[str, Any], ...]
    rule_sets: tuple[str, ...]
    visibility_floor: str
    #: Why the floor is what it is: ``[{reference, floor}]`` for each reference that raised it.
    floor_basis: tuple[dict[str, str], ...] = ()

    @property
    def path(self) -> str:
        return f"{DIRECTORY}/{self.code_sha256}.json"

    @property
    def cases_path(self) -> str:
        return f"{DIRECTORY}/{self.code_sha256}.cases.json"

    @property
    def content(self) -> bytes:
        return self.canonical.encode("utf-8")

    @property
    def cases_content(self) -> bytes:
        return canonical_json(list(self.cases))

    def listed(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "behaviour_key": self.behaviour_key,
            "code_sha256": self.code_sha256,
            "runner_contract": self.runner_contract,
            "path": self.path,
            "cases_path": self.cases_path,
            "cases_sha256": bytes_sha256(self.cases_content),
            "rule_sets": list(self.rule_sets),
            "visibility_floor": self.visibility_floor,
        }


def _query(version_id: str) -> Any:
    rule_set = ConfigVersion.__table__.alias("rs")
    return (
        select(
            ConfigVersion.name,
            ConfigVersion.number,
            CodeRule.behaviour_key,
            ConfigVersion.body_sha256,
            CodeRuleUse.runner_contract,
            ConfigVersion.body_canonical,
            ConfigVersion.body,
            rule_set.c.name,
            rule_set.c.number,
        )
        .join(CodeRuleUse, CodeRuleUse.code_rule_config_version_id == ConfigVersion.id)
        .join(CodeRule, CodeRule.name == ConfigVersion.name)
        .join(rule_set, rule_set.c.id == CodeRuleUse.rule_set_config_version_id)
        .join(VersionStep, VersionStep.step_execution_id == CodeRuleUse.step_execution_id)
        .where(VersionStep.version_id == version_id)
    )


# --- the visibility floor ------------------------------------------------------------------------


def _class_floor(classes: Sequence[Any]) -> str:
    """The floor licence classes set, read as C-1 reads them: a licence that forbids
    redistribution, else any that does not permit it (``private_only``, the class of an unknown or
    absent licence), else ``none``."""
    from ..publishing.licence_table import LicenceClass

    if any(c is LicenceClass.FORBIDS for c in classes):
        return "forbids_redistribution"
    if any(c is not LicenceClass.PERMITS for c in classes):
        return "private_only"
    return "none"


def _floor_of_reference(session: Session, reference: str) -> str:
    """One reference's floor: a version (its declared floor; its sources' licences), a source (its
    licence), or nothing (``private_only``: fail closed). A source licence counts as C-1 counts it
    (018 Review R3): one that does not permit redistribution keeps the rule private."""
    import uuid

    from ...models.source import Source
    from ..publishing.check_inputs import source_facts

    try:
        as_uuid = str(uuid.UUID(reference))
    except ValueError:
        return "private_only"  # not a version or source id: unresolvable (and never a DB error)
    version = session.get(Version, as_uuid)
    if version is not None:
        try:
            floor = _class_floor([f.current.licence_class for f in source_facts(session, version)])
        except LookupError:
            return "private_only"
        if floor == "none" and version.visibility_floor == "private_only":
            return "private_only"
        return floor
    source = session.get(Source, as_uuid)
    if source is not None:
        from ..publishing.check_inputs import _annotations, _refs
        from ..publishing.licence_table import classify

        found = classify(source.licence_raw, _refs(_annotations(session, source.id)))
        return _class_floor([found.licence_class])
    return "private_only"


def visibility_floor(
    session: Session, document: dict[str, Any], cases: Sequence[dict[str, Any]]
) -> tuple[str, tuple[dict[str, str], ...]]:
    """The strictest floor among ``derived_from.sources`` and the cases' source versions."""
    references = [str(r) for r in (document.get("derived_from") or {}).get("sources") or []]
    references += [
        str(c["source"]["source_version_id"])
        for c in cases
        if isinstance(c.get("source"), dict) and c["source"].get("source_version_id")
    ]
    floor, basis = "none", []
    for reference in dict.fromkeys(references):
        found = _floor_of_reference(session, reference)
        if found != "none":
            basis.append({"reference": reference, "floor": found})
        if FLOORS.index(found) > FLOORS.index(floor):
            floor = found
    return floor, tuple(basis)


# --- what a version carries ----------------------------------------------------------------------


def _carried(
    session: Session | None, rows: Sequence[Sequence[Any]], recorded: bool
) -> list[CarriedCodeRule] | None:
    by_sha: dict[str, dict[str, Any]] = {}
    for r in rows:
        sha = str(r[3])
        entry = by_sha.setdefault(
            sha,
            {
                "name": str(r[0]),
                "version": int(r[1]),
                "behaviour_key": str(r[2]),
                "runner_contract": str(r[4]),
                "canonical": str(r[5]),
                "cases": tuple(r[6]["cases"]),
                "rule_sets": set(),
            },
        )
        entry["rule_sets"].add(f"{r[7]}@{r[8]}")
    if not by_sha and not recorded:
        return None
    out = []
    for sha, e in sorted(by_sha.items(), key=lambda kv: (kv[1]["name"], kv[1]["version"])):
        if session is not None:
            floor, basis = visibility_floor(session, json.loads(e["canonical"]), e["cases"])
        else:
            floor, basis = "none", ()
        out.append(
            CarriedCodeRule(
                name=e["name"],
                version=e["version"],
                behaviour_key=e["behaviour_key"],
                code_sha256=sha,
                runner_contract=e["runner_contract"],
                canonical=e["canonical"],
                cases=e["cases"],
                rule_sets=tuple(sorted(e["rule_sets"])),
                visibility_floor=floor,
                floor_basis=basis,
            )
        )
    return out


def carried(session: Session, version: Version) -> list[CarriedCodeRule] | None:
    """The code rules the version's build ran, or None: "not recorded"."""
    rows = session.execute(_query(version.id)).all()
    return _carried(session, rows, bool(version.code_rules_recorded))


async def carried_async(db: AsyncSession, version: Version) -> list[CarriedCodeRule] | None:
    """As :func:`carried`, for a read (the floor is computed by the sync publish path only)."""
    rows = (await db.execute(_query(version.id))).all()
    return _carried(None, rows, bool(version.code_rules_recorded))


def listing(items: Sequence[CarriedCodeRule] | None) -> dict[str, Any] | None:
    """``{"recorded": true, "code_rules": [...]}`` validated against ``CodeRuleListing``; None
    when not recorded (the manifest then has no key: absence means "not recorded")."""
    if items is None:
        return None
    value = {"recorded": True, "code_rules": [c.listed() for c in items]}
    CodeRuleListing.model_validate(value)
    return value


def write_files(items: Sequence[CarriedCodeRule] | None, root: Path) -> list[dict[str, Any]]:
    """Write each carried rule's document (its exact hashed bytes) and cases under ``root``."""
    from ..publishing.digests import bytes_git_blob_sha1

    out: list[dict[str, Any]] = []
    for item in items or []:
        if bytes_sha256(item.content) != item.code_sha256:
            raise ValueError(f"{item.path}: stored bytes do not hash to the recorded code_sha256")
        for path, content, kind in (
            (item.path, item.content, "document"),
            (item.cases_path, item.cases_content, "cases"),
        ):
            atomic_write_bytes(root / path, content)
            out.append(
                {
                    "name": item.name,
                    "version": item.version,
                    "code_sha256": item.code_sha256,
                    "kind": kind,
                    "sha256": bytes_sha256(content),
                    "path": path,
                    "bytes": len(content),
                    "git_blob_sha1": bytes_git_blob_sha1(content),
                }
            )
    return out


async def read_block(db: AsyncSession, version: Version) -> dict[str, Any]:
    """What ``GET /versions/{id}`` shows: ``{recorded: false}``, or each carried rule with its
    approval state NOW (FR-018.42: a since-revoked rule says so; files and hashes never change)."""
    from . import approvals

    items = await carried_async(db, version)
    if items is None:
        return {"recorded": False, "code_rules": None}
    out = []
    for item in items:
        state = await approvals.state(db, item.code_sha256)
        out.append(
            {
                "name": item.name,
                "version": item.version,
                "code_sha256": item.code_sha256,
                "rule_sets": list(item.rule_sets),
                "path": item.path,
                "cases_path": item.cases_path,
                "approval_state": state,
                "note": (
                    "built with a since-revoked code rule; the files and hashes are unchanged"
                    if state == "revoked"
                    else None
                ),
            }
        )
    return {"recorded": True, "code_rules": out}
