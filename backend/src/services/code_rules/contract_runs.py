"""Each hash's cases under each runner contract version (FR-018.31; FTDD §5.5, §8.5).

A save writes the first row (its cases just passed at the floor under the runner's contract
version). The Beat task ``midataworks.code_rules.recheck_contract`` writes one for every stored
hash whose latest row is for another contract version, so a contract MINOR that breaks a stored
rule's case marks it "failing under contract <version>" without a new approval. The rows are a
history, never an overwritten column.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ...clients.runner_client import DocumentRef, RunnerClient, RunnerRefused
from ...models.code_rule import CodeRule, CodeRuleContractRun
from ...models.publish import ConfigVersion
from ...runner_contract.document import CodeRuleCase
from . import cases as case_runs
from . import limits
from .save import DOCUMENT_REFUSALS, capacity_exhausted
from .store import KIND

logger = logging.getLogger(__name__)


def out(row: CodeRuleContractRun | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "contract_version": row.contract_version,
        "bundle_digest": row.bundle_digest,
        "passed": row.passed,
        "failing_case": row.failing_case,
        "reason": row.reason,
        "run_at": row.run_at.isoformat() if row.run_at else None,
    }


async def latest(db: AsyncSession, code_sha256: str) -> CodeRuleContractRun | None:
    return (
        await db.execute(
            select(CodeRuleContractRun)
            .where(CodeRuleContractRun.code_sha256 == code_sha256)
            .order_by(CodeRuleContractRun.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def record(
    session: Session | AsyncSession,
    code_sha256: str,
    contract_version: str,
    bundle_digest: str,
    *,
    failing_case: str | None = None,
    reason: str | None = None,
) -> CodeRuleContractRun:
    row = CodeRuleContractRun(
        code_sha256=code_sha256,
        contract_version=contract_version,
        bundle_digest=bundle_digest,
        passed=failing_case is None and reason is None,
        failing_case=failing_case,
        reason=reason,
    )
    session.add(row)
    return row


def _stored_versions(session: Session) -> list[ConfigVersion]:
    """The newest version of each hash among unarchived code rules."""
    rows = session.execute(
        select(ConfigVersion)
        .join(CodeRule, CodeRule.name == ConfigVersion.name)
        .where(ConfigVersion.kind == KIND, CodeRule.archived_at.is_(None))
        .order_by(ConfigVersion.created_at.desc(), ConfigVersion.number.desc())
    ).scalars()
    newest: dict[str, ConfigVersion] = {}
    for row in rows:
        newest.setdefault(row.body_sha256, row)
    return list(newest.values())


def latest_for(
    session: Session, code_sha256: str, contract_version: str
) -> CodeRuleContractRun | None:
    """The hash's latest row for one contract version (sync; build resolution, rule-set save)."""
    return session.execute(
        select(CodeRuleContractRun)
        .where(
            CodeRuleContractRun.code_sha256 == code_sha256,
            CodeRuleContractRun.contract_version == contract_version,
        )
        .order_by(CodeRuleContractRun.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def check_version(
    session: Session,
    client: RunnerClient,
    version: ConfigVersion,
    contract_version: str,
    bundle_digest: str,
) -> tuple[CodeRuleContractRun | None, str | None]:
    """Run one stored version's cases at the floor under the runner's contract version and write the
    row: ``(row, None)``; or write NOTHING and say why: ``(None, <runner fault>)``. Only the RULE's
    failure is recorded (a document refusal, a failing case): a row is final for its contract
    version, so a runner fault (no free slot, a refused request) must never become one (018
    Review R2). Used by the Beat re-check and by build resolution's synchronous fallback (FTDD
    §17.4: no row yet for the runner's minor). The caller commits."""
    sha = version.body_sha256
    cases = [CodeRuleCase.model_validate(c) for c in version.body["cases"]]
    try:
        answer = client.run(
            DocumentRef(sha, str(version.body_canonical)),
            case_runs.calls_for(cases, 1),
            mode="save_check",
            lane="batch",
            limits=limits.floor(),
            send_document=True,
        )
    except RunnerRefused as exc:
        if exc.code not in DOCUMENT_REFUSALS:
            # A refusal of the REQUEST (a limit past the runner's ceiling, a body bound, the
            # contract major) is this deployment's fault, not the rule's (018 Review R2).
            logger.warning("code-rule contract check stopped: the runner refused %s", exc.code)
            return None, exc.code
        return record(session, sha, contract_version, bundle_digest, reason=exc.code), None
    if not answer.available or capacity_exhausted(answer.results):
        # The runner went away, or had no free slot for every case: nothing is known about the
        # rule. Write nothing; retry later.
        return None, "rule_worker_unavailable"
    bad = next((v for v in case_runs.verdicts(cases, answer.results) if not v.passed), None)
    return (
        record(
            session,
            sha,
            contract_version,
            bundle_digest,
            failing_case=bad.case.id if bad else None,
        ),
        None,
    )


def recheck(session: Session, client: RunnerClient) -> dict[str, Any]:
    """Run every stored hash's cases under the runner's CURRENT contract version, once.

    Returns counts. A runner that cannot be reached checks nothing and says so (the next pass
    retries); a rule whose case now fails gets a ``passed = false`` row naming the case. Only the
    RULE's failure is recorded: a runner fault (no free slot, a refused limit, a refused request)
    stops the pass with nothing written, because a row is final for its contract version."""
    health = client.health()
    if health is None or health.get("status") != "ok":
        return {"checked": 0, "failing": 0, "runner": "unavailable"}
    contract_version = str(health["contract_version"])
    bundle_digest = str(health["bundle_digest"])
    checked = failing = 0
    stopped: str | None = None
    for version in _stored_versions(session):
        sha = version.body_sha256
        last = session.execute(
            select(CodeRuleContractRun.contract_version)
            .where(CodeRuleContractRun.code_sha256 == sha)
            .order_by(CodeRuleContractRun.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if last == contract_version:
            continue
        row, fault = check_version(session, client, version, contract_version, bundle_digest)
        if row is None:
            stopped = fault
            break
        session.commit()
        checked += 1
        failing += not row.passed
    session.commit()
    logger.info(
        "code-rule contract re-check: %d checked, %d failing under %s",
        checked,
        failing,
        contract_version,
    )
    out: dict[str, Any] = {
        "checked": checked,
        "failing": failing,
        "contract_version": contract_version,
    }
    if stopped is not None:
        out["stopped"] = stopped
    return out
