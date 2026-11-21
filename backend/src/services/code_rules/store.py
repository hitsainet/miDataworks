"""Code-rule heads and versions in the versioned configuration store (FR-018.26; FTDD §5.2, §5.3).

A version is a ``dw_config_versions`` row of kind ``code_rule``: ``body`` = ``{document, cases,
code_sha256}``, ``body_canonical`` = the canonical document text, ``body_sha256`` = ``code_sha256``
(the database proves the text hashes to it). Versions of one name are numbered under a lock on the
name's head row (``SELECT ... FOR UPDATE``), so two concurrent saves never take one number - this
fixes, for this kind, the unlocked ``max(number) + 1`` of ``services/exports/config_versions.py``.
A name keeps one behaviour key (``code_rule_name_taken``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.agent_origin import Who
from ...core.ids import new_id
from ...models.code_rule import CodeRule
from ...models.publish import ConfigKind, ConfigVersion
from .documents import Parsed
from .errors import CodeRuleError, not_found

KIND = ConfigKind.CODE_RULE.value
PLUGIN = "code_rule"


def body_of(parsed: Parsed) -> dict[str, Any]:
    return {
        "document": parsed.raw,
        # As written: a field the author left out stays out (no ``null`` invented for it).
        "cases": [c.model_dump(mode="json", exclude_none=True) for c in parsed.cases],
        "code_sha256": parsed.code_sha256,
    }


def name_taken(name: str, existing_key: str) -> CodeRuleError:
    return CodeRuleError(
        409,
        "code_rule_name_taken",
        f"The name {name} belongs to behaviour key {existing_key}; a name has one key. Choose "
        "another name.",
        {"name": name, "behaviour_key": existing_key},
        next_tool="dataworks_list_code_rules",
        next_args={"behaviour_key": existing_key},
        why="see the code rules already under that key",
    )


async def head(db: AsyncSession, name: str) -> CodeRule | None:
    return await db.get(CodeRule, name)


async def check_name(db: AsyncSession, name: str, behaviour_key: str) -> None:
    existing = await head(db, name)
    if existing is not None and existing.behaviour_key != behaviour_key:
        raise name_taken(name, existing.behaviour_key)


@dataclass(frozen=True)
class Written:
    row: ConfigVersion
    created: bool


async def latest(db: AsyncSession, name: str) -> ConfigVersion | None:
    return (
        await db.execute(
            select(ConfigVersion)
            .where(ConfigVersion.kind == KIND, ConfigVersion.name == name)
            .order_by(ConfigVersion.number.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def create_version(
    db: AsyncSession, name: str, behaviour_key: str, parsed: Parsed, who: Who
) -> Written:
    """Version n+1 of ``name`` under the head-row lock; an identical body stores nothing."""
    await db.execute(
        pg_insert(CodeRule)
        .values(
            name=name,
            behaviour_key=behaviour_key,
            created_by=who.who,
            created_by_origin=who.origin,
        )
        .on_conflict_do_nothing(index_elements=["name"])
    )
    locked = (
        await db.execute(select(CodeRule).where(CodeRule.name == name).with_for_update())
    ).scalar_one()
    if locked.behaviour_key != behaviour_key:
        raise name_taken(name, locked.behaviour_key)
    previous = await latest(db, name)
    body = body_of(parsed)
    if previous is not None and previous.body == body:
        return Written(previous, False)
    row = ConfigVersion(
        id=new_id("crv"),
        kind=KIND,
        name=name,
        number=(previous.number if previous is not None else 0) + 1,
        plugin=PLUGIN,
        body=body,
        body_sha256=parsed.code_sha256,
        body_canonical=parsed.canonical,
        parent_id=previous.id if previous is not None else None,
        created_by=who.who,
        created_by_origin=who.origin,
    )
    db.add(row)
    await db.flush()
    return Written(row, True)


async def get_version(db: AsyncSession, name: str, number: int) -> ConfigVersion:
    row = (
        await db.execute(
            select(ConfigVersion).where(
                ConfigVersion.kind == KIND,
                ConfigVersion.name == name,
                ConfigVersion.number == number,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise not_found(name, number)
    return row


async def latest_by_hash(
    db: AsyncSession, code_sha256: str, *, include_archived: bool = False
) -> ConfigVersion | None:
    """The newest version carrying a hash (unarchived names first unless asked otherwise)."""
    query = (
        select(ConfigVersion)
        .join(CodeRule, CodeRule.name == ConfigVersion.name)
        .where(ConfigVersion.kind == KIND, ConfigVersion.body_sha256 == code_sha256)
        .order_by(ConfigVersion.created_at.desc(), ConfigVersion.number.desc())
        .limit(1)
    )
    if not include_archived:
        query = query.where(CodeRule.archived_at.is_(None))
    return (await db.execute(query)).scalar_one_or_none()


async def superseded_by(db: AsyncSession, row: ConfigVersion) -> int | None:
    later: int | None = (
        await db.execute(
            select(func.min(ConfigVersion.number)).where(
                ConfigVersion.kind == KIND,
                ConfigVersion.name == row.name,
                ConfigVersion.number > row.number,
            )
        )
    ).scalar_one_or_none()
    return later
