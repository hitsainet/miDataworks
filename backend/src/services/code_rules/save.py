"""Saving a code rule (FR-018.28, FR-018.31, FR-018.32; FTDD §7.3; FTID §5.2, I1).

``save_checks`` runs every check in order - the switch, the static checks, the name's key, the
runner's ``/v1/inspect`` (load refusals, declared functions exported), every case TWICE on fresh
instances at the contract FLOOR (``mode: save_check``) - and refuses at the first failure, naming
the item and a ``next``. It runs:

- in the decorator's ``enrich`` (:func:`approval_facts`) for an agent's save of a new hash, so a
  request that would be refused is refused at once and the operator only ever sees code that
  passes its own cases (FR-018.28);
- in the route body on EVERY path (the operator's save, an agent's save of an approved hash, the
  executor's replay after approval), because state may have moved since.

``hash_not_currently_approved`` is the decorator's ``when``: a fresh read of the approval list, so
a case-only edit of an approved hash needs no new approval (FR-018.31) and a new hash always does.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...clients.runner_client import AsyncRunnerClient, DocumentRef, RunnerRefused
from ...core.config import get_settings
from ...runner_contract.hashing import code_sha256
from ...runner_contract.reasons import Reason
from . import approvals, documents, limits, store
from . import cases as case_runs
from .documents import Parsed
from .errors import CodeRuleError, disabled, runner_unavailable

DIFF_MAX_BYTES = 65_536
TEST_TOOL = "dataworks_test_code_rule"


@dataclass(frozen=True)
class SaveFacts:
    """What the checks established: the facts an approval card shows and the save records."""

    parsed: Parsed
    imports: list[str]
    exported: list[str]
    case_results: list[dict[str, Any]]
    contract_version: str
    bundle_digest: str
    extra: dict[str, Any] = field(default_factory=dict)


#: Runner refusals of the DOCUMENT (the author's to correct); anything else the runner refuses is a
#: fault of this deployment or of the runner, never a verdict on the code (018 Review R2).
DOCUMENT_REFUSALS = frozenset(
    {"document_hash_mismatch", "code_rule_invalid", "function_not_declared"}
)
#: Runner refusals of a LIMIT this deployment sent: its setting is above the runner's RUNNER_MAX_*
#: ceiling (or below the floor). Named, so it never reads as "the runner cannot be reached".
LIMIT_REFUSALS = frozenset({"limit_above_ceiling", "limit_below_floor"})
LIMIT_NAME = re.compile(r"limits\.([a-z_]+)")
#: The runner limit and the setting that sends it (``limits.in_force``); prelude fuel has none.
LIMIT_SETTINGS = {
    "call_fuel": "CODE_RULE_CALL_FUEL",
    "load_fuel": "CODE_RULE_LOAD_FUEL",
    "memory_bytes": "CODE_RULE_MEMORY_BYTES",
    "input_code_points": "CODE_RULE_INPUT_CODE_POINTS",
    "output_bytes": "CODE_RULE_OUTPUT_BYTES",
    "wall_ms": "CODE_RULE_WALL_MS",
}


def limit_refused(exc: RunnerRefused) -> CodeRuleError:
    """The runner refused a limit this deployment sends (its setting is past the runner's ceiling)."""
    found = LIMIT_NAME.search(exc.message)
    limit = found.group(1) if found else None
    setting = LIMIT_SETTINGS.get(limit or "")
    return CodeRuleError(
        409,
        "runner_limit_refused",
        f"The runner refused the limit {limit or '(unnamed)'} this deployment sends ({exc.code})"
        + (f"; {setting} is set past what the runner admits" if setting else "")
        + ". Raise the runner's RUNNER_MAX_* ceiling (k8s/base/runner.yaml) before the setting, "
        "or lower the setting. Nothing was run or stored.",
        {"runner_code": exc.code, "limit": limit, "setting": setting},
        next_tool="dataworks_get_runner_contract",
        why="read the limits in force here",
    )


def capacity_exhausted(results: list[list[dict[str, Any]]]) -> bool:
    """Whether any result says the runner could not run it (a lane with no free slot before the
    request's wall-time backstop, or a guest it cannot start): a runner fault, never the code's."""
    unavailable = Reason.RULE_WORKER_UNAVAILABLE.value
    return any(r.get("reason") == unavailable for row in results for r in row)


def refused_by_runner(exc: RunnerRefused) -> CodeRuleError:
    """A runner refusal of a request the API built: the contract or the document, or the runner."""
    if exc.code in LIMIT_REFUSALS:
        return limit_refused(exc)
    if exc.code == "runner_contract_unknown":
        return CodeRuleError(
            422,
            "runner_contract_unknown",
            "The runner does not serve this contract major.",
            {"runner_code": exc.code},
            next_tool="dataworks_get_runner_contract",
            why="read the contract this deployment serves",
        )
    if exc.code in DOCUMENT_REFUSALS:
        return CodeRuleError(
            422,
            "code_rule_invalid",
            f"The runner refused the document ({exc.code}).",
            {"runner_code": exc.code},
            next_tool=TEST_TOOL,
            why="try the corrected document",
        )
    return runner_unavailable(f"the runner refused the request: {exc.code}")


async def _inspect(client: AsyncRunnerClient, parsed: Parsed) -> dict[str, Any]:
    try:
        answer = await client.inspect(DocumentRef(parsed.code_sha256, parsed.canonical))
    except RunnerRefused as exc:
        raise refused_by_runner(exc) from None
    if answer is None:
        raise runner_unavailable("no answer within the retry budget")
    if "functions" in answer:
        return answer
    reason = str(answer.get("reason"))
    diagnostic = answer.get("diagnostic") or {}
    if reason == "rule_worker_unavailable":
        raise runner_unavailable("the runner is up but cannot run guests (see its health)")
    if reason == "rule_load_refused":
        refused = answer.get("refused")
        raise CodeRuleError(
            422,
            "code_rule_load_refused",
            f"The source imports or names something the guest refuses ({refused}).",
            {"refused": refused, "reason": reason},
            next_tool="dataworks_get_runner_contract",
            why="read the modules a code rule may import (stdlib_allowed)",
        )
    raise CodeRuleError(
        422,
        "code_rule_load_refused",
        f"The source does not load ({reason}"
        + (
            f": {diagnostic.get('exception_type')} at line {diagnostic.get('line')}"
            if diagnostic
            else ""
        )
        + ").",
        {
            "reason": reason,
            "exception_type": diagnostic.get("exception_type"),
            "line": diagnostic.get("line"),
        },
        next_tool=TEST_TOOL,
        why="see the error on the dry run and correct the source",
    )


async def save_checks(
    name: str, behaviour_key: str, raw_document: Any, raw_cases: Any, db: AsyncSession
) -> SaveFacts:
    """Every save check, in order; the first failure is raised (FR-018.32)."""
    if not get_settings().code_rules_enabled:
        raise disabled()
    parsed = documents.parse(raw_document, raw_cases, require_cases=True)
    await store.check_name(db, name, behaviour_key)
    await refuse_revoked(db, parsed.code_sha256)
    client = limits.client()
    inspected = await _inspect(client, parsed)
    exported = list(inspected.get("functions") or [])
    for function in parsed.function_names:
        if function not in exported:
            raise CodeRuleError(
                422,
                "code_rule_function_missing",
                f"The document declares {function}, but the source does not define it.",
                {"function": function, "exported": exported},
                next_tool=TEST_TOOL,
                why="define the function or remove its declaration",
            )
    try:
        answer = await client.run(
            DocumentRef(parsed.code_sha256, parsed.canonical),
            case_runs.calls_for(parsed.cases, 2),
            mode="save_check",
            lane="interactive",
            limits=limits.floor(),
            send_document=True,
        )
    except RunnerRefused as exc:
        raise refused_by_runner(exc) from None
    if not answer.available:
        raise runner_unavailable("no answer within the retry budget")
    if capacity_exhausted(answer.results):
        # The lane backstop is per REQUEST: a long save's tail can read rule_worker_unavailable
        # while its head ran. That is the runner's capacity, never non-determinism or a failed
        # case of the author's (018 Review R2; the miStudio 040 S4 R2 analogue).
        raise runner_unavailable("the runner had no free slot for every case; nothing was stored")
    found = case_runs.verdicts(parsed.cases, answer.results)
    for verdict in found:
        if not verdict.deterministic:
            raise CodeRuleError(
                422,
                "code_rule_nondeterministic",
                f"Case {verdict.case.id} gave two different answers on two fresh instances; a code "
                "rule must give the same answer every time.",
                {"case": verdict.case.id},
                next_tool=TEST_TOOL,
                why="find what varies (state, ordering) and remove it",
            )
        if not verdict.passed:
            raise CodeRuleError(
                422,
                "code_rule_case_failed",
                f"Case {verdict.case.id} expected {case_runs.expected(verdict.case)} at the "
                f"contract floor and got {case_runs.outcome(verdict.results[0])}.",
                {
                    "case": verdict.case.id,
                    "expected": case_runs.expected(verdict.case),
                    "actual": case_runs.outcome(verdict.results[0]),
                },
                next_tool=TEST_TOOL,
                why="run the case on a dry run and correct the rule or the case",
            )
    return SaveFacts(
        parsed=parsed,
        imports=list(inspected.get("imports") or []),
        exported=exported,
        case_results=[{**v.out(), "result": case_runs.outcome(v.results[0])} for v in found],
        contract_version=str(answer.contract_version),
        bundle_digest=str(answer.bundle_digest),
    )


async def refuse_revoked(db: AsyncSession, sha: str) -> None:
    """ENH-006 D60: ANY save of a revoked hash is refused - the operator's own save and an agent's
    alike, a case-only edit too - so a revocation is undone on one path only: "approve again",
    with a reason and the Settings PIN (Review R2 F5). Read fresh, before the runner is asked."""
    if await approvals.state(db, sha) != "revoked":
        return
    stored = await store.latest_by_hash(db, sha, include_archived=True)
    raise CodeRuleError(
        409,
        "code_rule_revoked",
        f"The hash {sha} was revoked by the operator; a revoked code rule is never saved again. "
        "Only the operator restores it, with a reason and the Settings PIN (Approve again on the "
        "Code rules screen); or change the code, which makes a new hash.",
        {"code_sha256": sha, "state": "revoked"},
        next_tool="dataworks_get_code_rule" if stored is not None else "dataworks_list_code_rules",
        next_args=({"name": stored.name, "version": stored.number} if stored is not None else {}),
        why="read the revocation in its approval history",
    )


async def recheck_for_approval(row: Any) -> list[dict[str, Any]]:
    """ENH-006 D61: before "approve again" writes an ``approve`` row, the hash's STORED cases run
    again under the current contract floor (``save_check``, twice on fresh instances, as a save
    runs them). The runner unreachable or out of slots: 503, nothing written; a case failing now:
    409 ``code_rule_case_failed`` naming the case, nothing written. Returns the case results."""
    parsed = documents.parse(
        json.loads(str(row.body_canonical)), row.body["cases"], require_cases=True
    )
    client = limits.client()
    try:
        answer = await client.run(
            DocumentRef(parsed.code_sha256, parsed.canonical),
            case_runs.calls_for(parsed.cases, 2),
            mode="save_check",
            lane="interactive",
            limits=limits.floor(),
            send_document=True,
        )
    except RunnerRefused as exc:
        raise refused_by_runner(exc) from None
    if not answer.available:
        raise runner_unavailable("no answer within the retry budget; nothing was approved")
    if capacity_exhausted(answer.results):
        raise runner_unavailable("the runner had no free slot for every case; nothing was approved")
    found = case_runs.verdicts(parsed.cases, answer.results)
    for verdict in found:
        if not verdict.passed:
            raise CodeRuleError(
                409,
                "code_rule_case_failed",
                f"Case {verdict.case.id} of {row.name}@{row.number} expected "
                f"{case_runs.expected(verdict.case)} under the current contract floor and got "
                f"{case_runs.outcome(verdict.results[0])}; the hash stays revoked.",
                {
                    "case": verdict.case.id,
                    "expected": case_runs.expected(verdict.case),
                    "actual": case_runs.outcome(verdict.results[0]),
                    "deterministic": verdict.deterministic,
                    "contract_version": answer.contract_version,
                },
                next_tool=TEST_TOOL,
                next_args={"name": row.name, "version": row.number},
                why="see the stored rule fail its case on a dry run",
            )
    return [{**v.out(), "result": case_runs.outcome(v.results[0])} for v in found]


async def hash_not_currently_approved(values: dict[str, Any], db: AsyncSession) -> bool:
    """The decorator's ``when``: gate an agent's save unless its hash is approved NOW."""
    document = values["body"].document
    parsed_document, dumped = documents.parse_document(document)
    del parsed_document
    return await approvals.state(db, code_sha256(dumped)) != "approved"


def _diff(previous: str | None, current: str) -> str:
    if previous is None:
        return ""
    lines = difflib.unified_diff(
        previous.splitlines(keepends=True),
        current.splitlines(keepends=True),
        fromfile="previous",
        tofile="this",
    )
    text = "".join(lines)
    encoded = text.encode("utf-8")
    if len(encoded) > DIFF_MAX_BYTES:
        return encoded[:DIFF_MAX_BYTES].decode("utf-8", errors="ignore") + "\n[diff truncated]\n"
    return text


async def approval_facts(values: dict[str, Any], db: AsyncSession) -> dict[str, Any]:
    """The decorator's ``enrich``: run every check NOW (a refusal queues nothing) and return the
    facts the approval card shows. The diff reads the previous source as text, never as code."""
    body = values["body"]
    facts = await save_checks(body.name, body.behaviour_key, body.document, body.cases, db)
    previous = await store.latest(db, body.name)
    previous_source = (
        json.loads(previous.body_canonical)["source"]
        if previous is not None and previous.body_canonical
        else None
    )
    # The hash's standing when the agent asked (a snapshot: save_checks above already refused a
    # revoked hash, D60). The card reads the CURRENT state fresh, because a revocation written
    # while the request waits makes approving it a 409 code_rule_revoked (018 S5 review).
    sha = facts.parsed.code_sha256
    decisions = await approvals.history(db, sha)
    return {
        "facts": {
            "name": body.name,
            "behaviour_key": body.behaviour_key,
            "code_sha256": sha,
            "approval_state": await approvals.state(db, sha),
            "last_decision": decisions[0] if decisions else None,
            "imports": facts.imports,
            "functions": facts.parsed.function_names,
            "params_schema": facts.parsed.document.params_schema,
            "case_results": facts.case_results,
            "contract_version": facts.contract_version,
            "previous": (
                {"version": previous.number, "code_sha256": previous.body_sha256}
                if previous is not None
                else None
            ),
            "diff": _diff(previous_source, facts.parsed.document.source),
        }
    }


def summary(payload: dict[str, Any]) -> str:
    """The approval's one line: the name and the hash, never the source (FR-018.25)."""
    facts = payload.get("facts") or {}
    return (
        f"Save code rule {payload['body']['name']} "
        f"({str(facts.get('code_sha256', ''))[:12]}...)"
    )
