"""What a build step's code rules did: use records, the failure share and the reuse block (feature
018 slice 4; FTDD §5.6, §7.6, §7.7; FTID §3.5; decisions I7, I8 = ENH-006 D36).

:func:`record_for_step` runs inside ``step_ingest.ingest``, BEFORE its single commit, so a
``rule_set@1`` step naming code rules never completes without its ``dw_code_rule_uses`` rows
(FR-018.39). The rows come from what the build RESOLVED for the step (``code_rule_resolution``'s
``versions`` and ``approvals``: the exact version and the approve row in force), cross-checked against the
step's batch reports (``code_rule_uses``): a report naming a code rule the resolution did not, or a
resolution naming a version that is not stored, fails the step ``code_rule_use_unrecorded``.

Then, from the same reports:

- **failure share (I8).** Rows whose code-rule signal failed for an OPERATOR-CODE reason (anything
  but ``rule_worker_unavailable`` / ``rule_timeout``) over the step's rows; above
  ``CODE_RULE_FAILURE_SHARE`` the step fails ``code_rule_failure_share_exceeded``, naming the code
  rule and its leading reason. A host fault never counts, so with the runner down a build completes
  (FR-018.22);
- **reuse block (I7).** A step whose code rules met any host fault is written ``reuse_blocked``: its
  rows are not the rules' answer, so the next build computes the step afresh.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ...core.config import get_settings
from ...core.errors import AppError
from ...models.code_rule import CodeRuleUse
from ...models.publish import ConfigVersion
from ...models.step_execution import StepExecution
from ...runner_contract.reasons import HOST_FAULTS
from ..rules.step_resolution import OPERATOR
from ..rules.uses import expected_use
from . import resolution
from .store import KIND


def _unrecorded(message: str, details: dict[str, Any]) -> AppError:
    return AppError(message, code="code_rule_use_unrecorded", status_code=422, details=details)


def _reports(reports: Sequence[Any] | None) -> list[Mapping[str, Any]]:
    return [r for r in reports or [] if isinstance(r, Mapping)]


def record_for_step(
    session: Session,
    execution: StepExecution,
    pinned: Mapping[str, Any],
    resolved: Mapping[str, Any] | None,
    reports: Sequence[Any] | None,
    rows_in: int,
) -> list[dict[str, Any]]:
    """Insert the step's use rows, apply the failure share and the reuse block (in the caller's
    transaction); return the use payloads. A step naming no code rule records nothing."""
    if execution.operator_name != OPERATOR or not resolved:
        return []
    found_resolution = resolution.parse_param(resolved) or {}
    versions = dict(found_resolution.get(resolution.VERSIONS) or {})
    approvals = dict(found_resolution.get(resolution.APPROVALS) or {})
    reported: dict[str, dict[str, Any]] = {}
    rows = Counter[str]()
    for report in _reports(reports):
        for item in report.get("code_rule_uses") or []:
            sha = str(item["code_sha256"])
            if sha not in versions:
                raise _unrecorded(
                    "The step reported running a code rule its resolution did not name.",
                    {"code_sha256": sha},
                )
            merged = reported.setdefault(
                sha,
                {
                    "runner_contract": item.get("runner_contract"),
                    "contract_version": None,
                    "bundle_digest": None,
                    "failed_rows": 0,
                    "reasons": Counter[str](),
                    "host_fault": None,
                },
            )
            merged["contract_version"] = merged["contract_version"] or item.get("contract_version")
            merged["bundle_digest"] = merged["bundle_digest"] or item.get("bundle_digest")
            merged["failed_rows"] += int(item.get("failed_rows") or 0)
            merged["reasons"].update(
                {str(k): int(v) for k, v in (item.get("reasons") or {}).items()}
            )
            merged["host_fault"] = merged["host_fault"] or item.get("host_fault")
        counts = report.get("code_rule_rows") or {}
        rows.update({k: int(v) for k, v in counts.items()})
    if not versions:
        if reported:
            raise _unrecorded("The step reported code rules it was never resolved with.", {})
        return []
    rule_set_version_id, _ = expected_use(session, pinned)
    stored = {
        row.id: row
        for row in session.execute(
            select(ConfigVersion).where(
                ConfigVersion.kind == KIND, ConfigVersion.id.in_(list(versions.values()))
            )
        ).scalars()
    }
    payloads: list[dict[str, Any]] = []
    for sha, version_id in sorted(versions.items()):
        row = stored.get(str(version_id))
        if row is None or row.body_sha256 != sha or sha not in approvals:
            raise _unrecorded(
                f"The step names code rule {sha}, whose resolved version or approval is not stored.",
                {"code_sha256": sha, "version_id": version_id},
            )
        found = reported.get(sha, {})
        reasons = dict(found.get("reasons") or {})
        payload = {
            "code_sha256": sha,
            "name": row.name,
            "version": row.number,
            "runner_contract": str(
                found.get("runner_contract") or row.body["document"]["runner_contract"]
            ),
            "contract_version": found.get("contract_version"),
            "bundle_digest": found.get("bundle_digest"),
            "approval_row_id": int(approvals[sha]),
            "failed_rows": int(found.get("failed_rows") or 0),
            "reasons": reasons,
            "host_fault": found.get("host_fault"),
        }
        session.execute(
            pg_insert(CodeRuleUse)
            .values(
                step_execution_id=execution.id,
                code_sha256=sha,
                rule_set_config_version_id=rule_set_version_id,
                code_rule_config_version_id=row.id,
                runner_contract=payload["runner_contract"],
                contract_version=payload["contract_version"],
                bundle_digest=payload["bundle_digest"],
                approval_row_id=payload["approval_row_id"],
                failed_rows=payload["failed_rows"],
                reasons=reasons,
            )
            .on_conflict_do_nothing(index_elements=["step_execution_id", "code_sha256"])
        )
        payloads.append(payload)
    _failure_share(payloads, rows, rows_in)
    if rows.get("host_fault", 0) > 0 or any(p["host_fault"] for p in payloads):
        execution.reuse_blocked = True
    return payloads


def _failure_share(payloads: list[dict[str, Any]], rows: Counter[str], rows_in: int) -> None:
    """I8: only operator-code reasons count; above the setting the step fails, naming the rule."""
    failed = int(rows.get("operator_code_failed", 0))
    total = max(int(rows.get("rows", 0)), rows_in, 1)
    limit = get_settings().code_rule_failure_share
    if failed / total <= limit:
        return
    guest = [
        (p, Counter({r: n for r, n in p["reasons"].items() if r not in HOST_FAULTS}))
        for p in payloads
    ]
    worst, reasons = max(guest, key=lambda item: (sum(item[1].values()), item[0]["code_sha256"]))
    leading = reasons.most_common(1)[0][0] if reasons else "unknown"
    raise AppError(
        f"Code rule {worst['name']}@{worst['version']} failed on {failed} of {total} rows "
        f"({failed / total:.2%}), above CODE_RULE_FAILURE_SHARE ({limit:.2%}); the leading "
        f"reason is {leading}. No row was written as 'did not fire'.",
        code="code_rule_failure_share_exceeded",
        status_code=409,
        details={
            "code_rule": f"{worst['name']}@{worst['version']}",
            "code_sha256": worst["code_sha256"],
            "leading_reason": leading,
            "failed_rows": failed,
            "rows": total,
            "share": failed / total,
            "setting": "CODE_RULE_FAILURE_SHARE",
            "limit": limit,
            "next": {
                "tool": "dataworks_test_code_rule",
                "args": {"name": worst["name"], "version": worst["version"]},
                "why": "try the rule on texts like the failing rows and see the reason",
            },
        },
    )
