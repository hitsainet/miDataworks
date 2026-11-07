"""Reading code rules (FR-018.33, FR-018.34): list, one version, by hash, the contract.

Each read states what is known and what is not: an approval state read now, the latest contract
run (or ``None``, "not recorded"), and ``used_by``: every version whose build ran this exact code-rule
version (``dw_code_rule_uses`` through ``dw_version_steps``; slice 4).
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import get_settings
from ...models.code_rule import CodeRule
from ...models.publish import ConfigVersion
from ...rules.next_step import next_for
from . import approvals, contract_runs, limits, store
from .store import KIND


async def used_by(db: AsyncSession, config_version_id: str) -> dict[str, Any]:
    """Every version whose build ran this code-rule version (FR-018.33), with the rule set that
    called it and the step's failed rows."""
    from ...models.code_rule import CodeRuleUse
    from ...models.version import Version, VersionStep

    rule_set = ConfigVersion.__table__.alias("rs")
    rows = (
        await db.execute(
            select(
                Version.id,
                Version.dataset_id,
                Version.number,
                VersionStep.step_index,
                rule_set.c.name,
                rule_set.c.number,
                CodeRuleUse.failed_rows,
            )
            .join(VersionStep, VersionStep.step_execution_id == CodeRuleUse.step_execution_id)
            .join(Version, Version.id == VersionStep.version_id)
            .join(rule_set, rule_set.c.id == CodeRuleUse.rule_set_config_version_id)
            .where(CodeRuleUse.code_rule_config_version_id == config_version_id)
            .order_by(Version.created_at, VersionStep.step_index)
        )
    ).all()
    return {
        "recorded": True,
        "versions": [
            {
                "version_id": str(r[0]),
                "dataset_id": str(r[1]),
                "number": r[2],
                "step_index": r[3],
                "rule_set": f"{r[4]}@{r[5]}",
                "failed_rows": r[6],
            }
            for r in rows
        ],
    }


def _functions(row: ConfigVersion) -> list[str]:
    return [f["name"] for f in row.body["document"]["functions"]]


async def list_code_rules(
    db: AsyncSession,
    *,
    page: int,
    limit: int,
    behaviour_key: str | None,
    include_archived: bool,
) -> dict[str, Any]:
    heads = select(CodeRule)
    if behaviour_key is not None:
        heads = heads.where(CodeRule.behaviour_key == behaviour_key)
    if not include_archived:
        heads = heads.where(CodeRule.archived_at.is_(None))
    total = (await db.execute(select(func.count()).select_from(heads.subquery()))).scalar_one()
    rows = list(
        (
            await db.execute(heads.order_by(CodeRule.name).offset((page - 1) * limit).limit(limit))
        ).scalars()
    )
    latest = {
        r.name: r
        for r in (
            await db.execute(
                select(ConfigVersion)
                .where(ConfigVersion.kind == KIND, ConfigVersion.name.in_([h.name for h in rows]))
                .order_by(ConfigVersion.name, ConfigVersion.number)
            )
        ).scalars()
    }
    items = []
    for head in rows:
        version = latest.get(head.name)
        sha = version.body_sha256 if version else None
        items.append(
            {
                "name": head.name,
                "behaviour_key": head.behaviour_key,
                "version": version.number if version else None,
                "code_sha256": sha,
                "approval_state": await approvals.state(db, sha) if sha else "unknown",
                "functions": _functions(version) if version else [],
                "last_case_result": (
                    contract_runs.out(await contract_runs.latest(db, sha)) if sha else None
                ),
                "archived": head.archived_at is not None,
            }
        )
    first = items[0] if items else None
    return {
        "items": items,
        "page": page,
        "limit": limit,
        "total": int(total),
        "next": (
            next_for(
                "dataworks_get_code_rule",
                {"name": first["name"], "version": first["version"]},
                "read a code rule's document, cases and approval",
            )
            if first and first["version"]
            else next_for("dataworks_test_code_rule", {}, "write a code rule and try it")
        ),
    }


async def approval_summary(db: AsyncSession, sha: str) -> dict[str, Any]:
    history = await approvals.history(db, sha)
    return {
        "state": await approvals.state(db, sha),
        "pending_approvals": await approvals.pending(db, sha),
        "history": history[:10],
        "history_rows": len(history),
    }


async def get_version(db: AsyncSession, name: str, version: int) -> dict[str, Any]:
    row = await store.get_version(db, name, version)
    head = await db.get(CodeRule, name)
    assert head is not None
    sha = row.body_sha256
    return {
        "name": row.name,
        "version": row.number,
        "behaviour_key": head.behaviour_key,
        "code_sha256": sha,
        "runner_contract": row.body["document"]["runner_contract"],
        "document": row.body["document"],
        "cases": row.body["cases"],
        "approval": await approval_summary(db, sha),
        "contract_status": contract_runs.out(await contract_runs.latest(db, sha)),
        "superseded_by": await store.superseded_by(db, row),
        "archived": head.archived_at is not None,
        "created_by": row.created_by,
        "created_by_origin": row.created_by_origin,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "used_by": await used_by(db, row.id),
        "next": next_for(
            "dataworks_test_code_rule",
            {"name": row.name, "version": row.number},
            "try the stored rule on a text",
        ),
    }


async def by_hash(db: AsyncSession, sha: str) -> dict[str, Any]:
    """What a consumer resolves a hash to (miStudio 040 S4 reads it): the exact hashed text."""
    row = await store.latest_by_hash(db, sha, include_archived=True)
    if row is None or row.body_canonical is None:
        from .errors import CodeRuleError

        raise CodeRuleError(
            404,
            "code_rule_not_found",
            f"No stored code rule carries the hash {sha}.",
            {"code_sha256": sha},
            next_tool="dataworks_list_code_rules",
            why="find a stored code rule",
        )
    return {
        "sha256": sha,
        "document": json.loads(row.body_canonical),
        "canonical_utf8": row.body_canonical,
        "cases": row.body["cases"],
        "name": row.name,
        "version": row.number,
        "approval_state": await approvals.state(db, sha),
    }


def runner_contract() -> dict[str, Any]:
    """FR-018.34: the contract's values and the settings in force here."""
    c = limits.contract()
    settings = get_settings()
    return {
        "format": c["format"],
        "contract_version": c["contract_version"],
        "language": c["language"],
        "stdlib_allowed": c["stdlib_allowed"],
        "limits_floor": c["limits_floor"],
        "limits_floor_status": c["limits_floor_status"],
        "limits_in_force": limits.in_force(),
        "count_cap": c["count_cap"],
        "reasons": c["reasons"],
        "diagnostic": c["diagnostic"],
        "dry_run": {
            "per_minute": settings.code_rule_dry_run_per_minute,
            "max_texts": settings.code_rule_dry_run_max_texts,
        },
        "caps": {
            "source_bytes": settings.code_rule_max_source_bytes,
            "functions": settings.code_rule_max_functions,
            "cases": settings.code_rule_max_cases,
            "param_schema_bytes": settings.code_rule_max_param_schema_bytes,
        },
        "code_rules_enabled": settings.code_rules_enabled,
        "next": next_for(
            "dataworks_test_code_rule", {}, "try a code-rule document on its cases and your texts"
        ),
    }
