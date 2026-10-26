"""Code-rule routes (feature 018; FTDD §6.1; FTID §5): dry run, gated save, list, get, by hash,
archive, and the operator's approval list.

- ``POST /code-rules/test`` - a dry run (FR-018.27): no approval, nothing stored, rate-limited.
- ``POST /code-rules`` - the save (FR-018.28), gated for agents by ``code_rule_save`` unless the
  document's hash is approved now. Static checks run before anything is queued (``before``), the
  runner and database checks in ``enrich`` (a refusal queues nothing), and all of them again in
  the body on every path. The operator's own save, and an approved agent save, write the
  ``approve`` row; an agent never does (the database CHECK, M14).
- ``GET /code-rules``, ``GET /code-rules/{name}/versions/{version}``, archive, unarchive
  (FR-018.33); ``GET /code-rules/by-hash/{sha}`` - what a consumer resolves a hash to (miStudio 040).
- ``GET /code-rules/approvals``; ``POST /code-rules/approvals/{sha}/revoke`` and ``/approve`` -
  the operator's ALONE (FR-018.30): agents get 403 ``agent_forbidden``, and the Settings PIN applies
  when one is set. No MCP tool calls them.

The fixed paths (``test``, ``approvals``, ``by-hash``) are declared before ``/{name}/...``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import (
    Actor,
    get_actor,
    refusing_agents,
    requires_approval_when_agent,
    resolve_who,
)
from ....core.database import get_db
from ....core.settings_pin import SETTINGS_PIN
from ....models.approval import Approval
from ....rules.next_step import next_for
from ....rules.rule_set import NAME_PATTERN
from ....schemas.archive import ArchiveRequest
from ....schemas.behaviour import BEHAVIOUR_KEY_PATTERN
from ....schemas.code_rules import ApprovalActionIn, CodeRuleSaveIn, CodeRuleTestIn
from ....services import archive_service
from ....services.code_rules import (
    approvals,
    contract_runs,
    documents,
    dry_run,
    save,
    store,
    views,
)
from ....services.code_rules.errors import CodeRuleError
from ._paging import Page, paging

router = APIRouter(prefix="/api/v1/code-rules", tags=["code_rules"])

SHA256 = r"^[0-9a-f]{64}$"
NamePath = Path(..., pattern=NAME_PATTERN)
ShaPath = Path(..., pattern=SHA256)
#: FR-018.30: approve and revoke are the operator's; an agent (MCP, miStudio's proxy) is refused.
operator_only = refusing_agents(
    "Only the operator approves or revokes a code rule, on the Code rules screen (FR-018.30)."
)


@router.post("/test")
async def test_code_rule(body: CodeRuleTestIn) -> dict[str, Any]:
    """Run a document (inline, with its cases) or a stored version on its cases and on up to
    CODE_RULE_DRY_RUN_MAX_TEXTS texts. Unapproved code may be tried; nothing is stored."""
    return await dry_run.run(
        document=body.document,
        cases=body.cases,
        name=body.name,
        version=body.version,
        texts=body.texts,
        params=body.params,
        functions=body.functions,
    )


def _pending_next(payload: dict[str, Any], approval_id: str) -> dict[str, Any]:
    return next_for(
        "dataworks_get_approval_status",
        {"approval_id": approval_id},
        "the operator reads the source and its case results; poll until it is decided",
    )


@router.post("", status_code=201)
@requires_approval_when_agent(
    "code_rule_save",
    when=save.hash_not_currently_approved,
    before=documents.save_checks_static,
    enrich=save.approval_facts,
    summary=save.summary,
    pending_next=_pending_next,
)
async def save_code_rule(
    body: CodeRuleSaveIn,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> dict[str, Any]:
    """Save a code rule (version n+1 of its name). Every check runs again here, whatever path
    led here; the approve row is written for the operator's save and an approved agent save."""
    who = await resolve_who(actor, db)
    facts = await save.save_checks(body.name, body.behaviour_key, body.document, body.cases, db)
    sha = facts.parsed.code_sha256
    state = await approvals.state(db, sha)
    if state == "revoked":
        # D60, read again AFTER the runner checks (seconds): a revocation written meanwhile must
        # not be undone by this save's approve row (018 Review R3).
        await save.refuse_revoked(db, sha)
    approver: tuple[str, str | None, str] | None = None
    if actor.origin == "operator":
        approver = (who.who, None, "saved by the operator")
    elif actor.approval_id is not None:
        request = await db.get(Approval, actor.approval_id)
        assert request is not None and request.decided_by
        approver = (
            request.decided_by,
            actor.approval_id,
            f"approved the agent's request {actor.approval_id}",
        )
    elif state != "approved":
        # The gate let it through as approved, and it was revoked since: refuse, store nothing.
        raise CodeRuleError(
            409,
            "code_rule_not_approved",
            f"The hash {sha} is {state} now; save it again to ask the operator.",
            {"code_sha256": sha, "state": state},
            next_tool="dataworks_save_code_rule",
            why="ask the operator again",
        )
    written = await store.create_version(db, body.name, body.behaviour_key, facts.parsed, who)
    if approver is not None and state != "approved":
        await approvals.write(db, sha, "approve", approver[2], approver[0], approver[1])
    last = await contract_runs.latest(db, sha)
    if last is None or last.contract_version != facts.contract_version:
        contract_runs.record(db, sha, facts.contract_version, facts.bundle_digest)
    await db.commit()
    row = written.row
    return {
        "id": row.id,
        "name": row.name,
        "version": row.number,
        "behaviour_key": body.behaviour_key,
        "code_sha256": sha,
        "created": written.created,
        "approval_state": await approvals.state(db, sha),
        "case_results": facts.case_results,
        "next": next_for(
            "dataworks_get_code_rule",
            {"name": row.name, "version": row.number},
            "read the stored version, its approval and its contract status",
        ),
    }


@router.get("/approvals")
async def list_approvals(
    code_sha256: str = Query(..., pattern=SHA256), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """A hash's approval state, its waiting agent requests and its full history."""
    summary = await views.approval_summary(db, code_sha256)
    summary["history"] = await approvals.history(db, code_sha256)
    return {"code_sha256": code_sha256, **summary}


async def _decide(
    db: AsyncSession, actor: Actor, sha: str, action: str, reason: str
) -> dict[str, Any]:
    who = await resolve_who(actor, db)
    stored = await store.latest_by_hash(db, sha, include_archived=True)
    if stored is None:
        raise CodeRuleError(
            404,
            "code_rule_not_found",
            f"No stored code rule carries the hash {sha}.",
            {"code_sha256": sha},
            next_tool="dataworks_list_code_rules",
            why="find a stored code rule",
        )
    state = await approvals.state(db, sha)
    changed = False
    case_results: list[dict[str, Any]] | None = None
    if action == "revoke" and state == "approved":
        await approvals.write(db, sha, "revoke", reason, who.who)
        changed = True
    elif action == "approve" and state != "approved":
        # D61: the stored cases pass under the current contract floor first, or nothing changes.
        case_results = await save.recheck_for_approval(stored)
        await approvals.write(db, sha, "approve", reason, who.who)
        changed = True
    elif action == "revoke":
        raise CodeRuleError(
            409,
            "code_rule_not_approved",
            f"The hash {sha} is {state}; only an approved hash can be revoked.",
            {"code_sha256": sha, "state": state},
            next_tool="dataworks_get_code_rule",
            next_args={"name": stored.name, "version": stored.number},
            why="read its approval history",
        )
    await db.commit()
    out: dict[str, Any] = {
        "code_sha256": sha,
        "state": await approvals.state(db, sha),
        "changed": changed,
        "name": stored.name,
        "version": stored.number,
    }
    if case_results is not None:
        out["case_results"] = case_results
    return out


@router.post("/approvals/{code_sha256}/revoke", dependencies=[SETTINGS_PIN])
async def revoke_code_rule(
    body: ApprovalActionIn,
    code_sha256: str = ShaPath,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(operator_only),
) -> dict[str, Any]:
    """Revoke an approved hash (the operator's alone). Tests and builds refuse it from now on;
    versions built with it keep their record."""
    return await _decide(db, actor, code_sha256, "revoke", body.reason)


@router.post("/approvals/{code_sha256}/approve", dependencies=[SETTINGS_PIN])
async def approve_code_rule_again(
    body: ApprovalActionIn,
    code_sha256: str = ShaPath,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(operator_only),
) -> dict[str, Any]:
    """Approve a stored hash again after a revocation (the operator's alone): the ONE way a
    revoked hash returns (D60). Its stored cases run again under the current contract floor
    first, and a failing case or an unreachable runner changes nothing (D61)."""
    return await _decide(db, actor, code_sha256, "approve", body.reason)


@router.get("/by-hash/{code_sha256}")
async def get_code_rule_by_hash(
    code_sha256: str = ShaPath, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """The newest stored version carrying a hash: ``{sha256, document, canonical_utf8, cases}``
    (the exact hashed text, so a consumer re-hashes and compares) and its approval state."""
    return await views.by_hash(db, code_sha256)


@router.get("")
async def list_code_rules(
    page: Page = Depends(paging),
    behaviour_key: str | None = Query(None, pattern=BEHAVIOUR_KEY_PATTERN),
    include_archived: bool = False,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """One row per name with its latest version, hash, approval state, functions and last case
    result, by name, paged; archived hidden by default."""
    return await views.list_code_rules(
        db,
        page=page.page,
        limit=page.limit,
        behaviour_key=behaviour_key,
        include_archived=include_archived,
    )


@router.get("/{name}/versions/{version}")
async def get_code_rule(
    name: str = NamePath,
    version: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """One version: document, cases, hash, approval state and history, contract status,
    superseded_by."""
    return await views.get_version(db, name, version)


async def _set_archived(
    db: AsyncSession, actor: Actor, name: str, body: ArchiveRequest | None, archived: bool
) -> dict[str, Any]:
    who = await resolve_who(actor, db)
    row = await archive_service.set_archived(
        db, "code_rule", name, who, archived=archived, reason=body.reason if body else None
    )
    return {
        "name": row.name,
        "behaviour_key": row.behaviour_key,
        "archived": row.archived_at is not None,
        "archived_at": row.archived_at.isoformat() if row.archived_at else None,
        "next": next_for(
            "dataworks_list_code_rules",
            {"include_archived": True},
            "list code rules, archived ones included",
        ),
    }


@router.post("/{name}/archive")
async def archive_code_rule(
    name: str = NamePath,
    body: ArchiveRequest | None = None,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> dict[str, Any]:
    """Hide the code rule (every version) from lists. Nothing is deleted; its approval stands."""
    return await _set_archived(db, actor, name, body, True)


@router.post("/{name}/unarchive")
async def unarchive_code_rule(
    name: str = NamePath,
    body: ArchiveRequest | None = None,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> dict[str, Any]:
    return await _set_archived(db, actor, name, body, False)
