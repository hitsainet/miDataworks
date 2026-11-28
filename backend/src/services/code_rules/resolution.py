"""Code rules inside rule sets: the checks, the build-time resolution and the case runs (feature 018
slice 4; FTDD §7.5; FTID §3.5; FR-018.37, FR-018.39).

:class:`CodeRules` is the sync view a rule-set save, a rule-set preview and a build's step
resolution share. Every read is FRESH (no cache, M15/M19): a revocation written between two build
steps is honoured by the second (FR-018.30).

**Per code-rule signal, in order (FTID §3.5)** - :meth:`CodeRules.check_signal`:

1. ``code_rules_disabled`` - the switch is off (the old ``code_rule_not_available``'s replacement
   when off, FR-018.37);
2. ``code_rule_not_approved`` - the hash is ``unknown``, ``pending`` (an agent's save waiting) or
   ``revoked``; a hash with no stored version is ``unknown``;
3. ``code_rule_failing_under_contract`` - its cases fail under the runner's CURRENT contract
   version; with no row yet for that version the cases run now and the row is written (FTDD §17.4,
   the synchronous fallback);
4. ``code_rule_function_missing`` - the signal's function is not declared by the document;
5. ``code_rule_params_invalid`` - the signal's ``params`` fail the document's ``params_schema``
   (``jsonschema``: data validation, never code).

``code_rule_in_within_n`` and ``code_rule_positions_needed`` are the compiler's
(``rules.compose``), so every app refuses them alike.

**Resolution (a build step, FTDD §7.5)** adds ONE parameter, ``code_rule_resolution``, inside the
step identity: the canonical JSON text of ``documents`` ``{sha: canonical text}``, ``versions``
``{sha: version id}``, ``approvals`` ``{sha: approve row id}`` and ``runner_limits`` (the limits in
force). One string, because an operator's parameters are flat scalars (``operators/schema_subset``;
FTID §3.5 named four - Discrepancy recorded). A raised fuel limit can turn a
``rule_resource_limit`` row into a count, so a build under other limits never reuses the step. With the runner unreachable, nothing that needs it is decided here: the rows
read ``rule_worker_unavailable`` (FR-018.22) and the step is never reused (I7).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import jsonschema
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...clients.runner_client import DocumentRef, RunnerClient
from ...core.canonical_json import canonical_json
from ...core.config import get_settings
from ...core.errors import AppError
from ...models.code_rule import CodeRule
from ...models.publish import ConfigVersion
from ...rules.compose import CodeValue, CompiledRuleSet
from ...rules.rule_set import CodeRuleSignal, RuleSetDocument
from ..rules.errors import RuleSetError
from . import approvals, contract_runs, dispatch, limits
from .store import KIND

logger = logging.getLogger(__name__)

#: The parameter a build fills in for a rule set naming code rules (a recipe never sets it): the
#: canonical JSON text of these four keys.
PARAM = "code_rule_resolution"
DOCUMENTS = "documents"
VERSIONS = "versions"
APPROVALS = "approvals"
LIMITS = "runner_limits"
RESOLVED_PARAMS = (PARAM,)


def parse_param(params: Mapping[str, Any]) -> dict[str, Any] | None:
    """The resolution a step carries, or None (a rule set without code rules, or unresolved)."""
    value = params.get(PARAM)
    if not isinstance(value, str):
        return None
    parsed: dict[str, Any] = json.loads(value)
    return parsed


def code_signals(document: RuleSetDocument) -> list[CodeRuleSignal]:
    return [s for s in document.signals if isinstance(s, CodeRuleSignal)]


@dataclass(frozen=True)
class Stored:
    """One code-rule version a signal resolves to: what the build runs and records."""

    version_id: str
    name: str
    number: int
    canonical: str
    document: dict[str, Any]
    approve_row_id: int


class RunnerDown(AppError):
    """The runner is needed for a synchronous answer (save, preview) and is not there."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"The code-rule runner cannot be reached ({detail}); nothing was stored. Try again "
            "later; builds read rule_worker_unavailable for code-rule rows meanwhile.",
            code="runner_unavailable",
            status_code=503,
            details={
                "runner": detail,
                "next": {
                    "tool": "dataworks_health",
                    "args": {},
                    "why": "see whether the runner is up",
                },
            },
        )


class CodeRules:
    """Fresh, sync reads and runner calls for code-rule signals (see the module docstring)."""

    def __init__(
        self,
        session: Session,
        client_factory: Callable[[], RunnerClient] | None = None,
    ) -> None:
        self.session = session
        self._factory = client_factory or limits.sync_client
        self._client: RunnerClient | None = None
        self._client_known = False
        self._health: dict[str, Any] | None = None
        self._health_known = False

    # --- the runner ---------------------------------------------------------------------------

    def client(self) -> RunnerClient | None:
        """The runner client, or None when ``RUNNER_URL`` is not configured."""
        if not self._client_known:
            self._client_known = True
            try:
                self._client = self._factory()
            except AppError:
                self._client = None
        return self._client

    def health(self) -> dict[str, Any] | None:
        """The runner's ``/health`` body when it is serving (``status: ok``), else None."""
        if not self._health_known:
            self._health_known = True
            client = self.client()
            body = client.health() if client is not None else None
            self._health = body if body is not None and body.get("status") == "ok" else None
        return self._health

    # --- reads --------------------------------------------------------------------------------

    def stored(self, sha: str) -> ConfigVersion | None:
        """The newest version carrying the hash, an unarchived name's first (FTDD §7.5). Archiving
        hides a code rule from lists and keeps its approval (FR-018.33), so an archived name's
        version still resolves: archiving never breaks a build."""
        return self.session.execute(
            select(ConfigVersion)
            .join(CodeRule, CodeRule.name == ConfigVersion.name)
            .where(ConfigVersion.kind == KIND, ConfigVersion.body_sha256 == sha)
            .order_by(
                CodeRule.archived_at.is_(None).desc(),
                ConfigVersion.created_at.desc(),
                ConfigVersion.number.desc(),
            )
            .limit(1)
        ).scalar_one_or_none()

    def approval(self, sha: str) -> tuple[str, int | None]:
        """``(state, approve row id)``: ``approved``, ``pending``, ``revoked`` or ``unknown``."""
        state, row_id = approvals.latest_sync(self.session, sha)
        if state == "approved":
            return state, row_id
        if state == "unknown" and approvals.pending_sync(self.session, sha):
            return "pending", None
        return state, None

    # --- the checks of one signal (FTID §3.5) -------------------------------------------------

    def check_signal(self, signal: CodeRuleSignal, *, run_contract: bool = True) -> Stored:
        """Every check of one code-rule signal, in order; the first failure is raised."""
        if not get_settings().code_rules_enabled:
            raise RuleSetError(
                409,
                "code_rules_disabled",
                f"Signal {signal.id!r} is a code rule, and code rules are switched off in this "
                "deployment (CODE_RULES_ENABLED). Only the operator turns them on.",
                {"signal": signal.id, "setting": "CODE_RULES_ENABLED"},
                next_tool="dataworks_howto",
                next_args={"topic": "code_rules"},
                why="read how code rules are switched on and by whom",
            )
        sha = signal.code_sha256
        row = self.stored(sha)
        state, approve_row = self.approval(sha)
        if state != "approved" or approve_row is None or row is None:
            raise not_approved(signal.id, sha, state if state != "approved" else "unknown")
        self._contract(signal, row, run=run_contract)
        document: dict[str, Any] = json.loads(str(row.body_canonical))
        declared = [f["name"] for f in document["functions"]]
        if signal.function not in declared:
            raise RuleSetError(
                422,
                "code_rule_function_missing",
                f"Signal {signal.id!r} calls {signal.function!r}, which code rule {row.name}@"
                f"{row.number} does not declare ({', '.join(declared)}).",
                {"signal": signal.id, "function": signal.function, "declared": declared},
                next_tool="dataworks_get_code_rule",
                next_args={"name": row.name, "version": row.number},
                why="read the functions the code rule declares",
            )
        errors = sorted(
            jsonschema.Draft202012Validator(document["params_schema"]).iter_errors(signal.params),
            key=str,
        )
        if errors:
            pointer = "/" + "/".join(str(p) for p in errors[0].absolute_path)
            raise RuleSetError(
                422,
                "code_rule_params_invalid",
                f"Signal {signal.id!r}: its params fail the code rule's params_schema at "
                f"{pointer if pointer != '/' else '(root)'}: {errors[0].validator} "
                f"{errors[0].validator_value!r}.",
                {"signal": signal.id, "pointer": pointer, "keyword": errors[0].validator},
                next_tool="dataworks_get_code_rule",
                next_args={"name": row.name, "version": row.number},
                why="read the code rule's params_schema",
            )
        return Stored(
            version_id=row.id,
            name=row.name,
            number=row.number,
            canonical=str(row.body_canonical),
            document=document,
            approve_row_id=approve_row,
        )

    def _contract(self, signal: CodeRuleSignal, row: ConfigVersion, *, run: bool) -> None:
        """Refuse a code rule failing under the runner's CURRENT contract version (FR-018.31).
        Unknown (runner down, or ``run`` false with no row) decides nothing here."""
        health = self.health()
        if health is None:
            return
        contract_version = str(health["contract_version"])
        found = contract_runs.latest_for(self.session, row.body_sha256, contract_version)
        if found is None and run:
            client = self.client()
            assert client is not None  # health() answered through it
            found, fault = contract_runs.check_version(
                self.session, client, row, contract_version, str(health["bundle_digest"])
            )
            if found is not None:
                self.session.commit()
            else:
                logger.warning(
                    "code rule %s: contract check under %s not decided (%s)",
                    row.body_sha256[:12],
                    contract_version,
                    fault,
                )
        if found is not None and not found.passed:
            raise RuleSetError(
                422,
                "code_rule_failing_under_contract",
                f"Signal {signal.id!r}: code rule {row.name}@{row.number} fails "
                + (f"its case {found.failing_case!r}" if found.failing_case else found.reason or "")
                + f" under runner contract {contract_version}; new builds refuse it.",
                {
                    "signal": signal.id,
                    "code_sha256": row.body_sha256,
                    "contract_version": contract_version,
                    "case": found.failing_case,
                    "reason": found.reason,
                },
                next_tool="dataworks_test_code_rule",
                next_args={"name": row.name, "version": row.number},
                why="run the stored rule on its cases and see which one fails",
            )

    def check_document(self, document: RuleSetDocument, **kw: Any) -> dict[str, Stored]:
        """:meth:`check_signal` for every code-rule signal; ``{signal id: stored version}``."""
        return {s.id: self.check_signal(s, **kw) for s in code_signals(document)}

    # --- values -------------------------------------------------------------------------------

    def values(
        self,
        document: RuleSetDocument,
        stored: Mapping[str, Stored],
        texts: Sequence[str],
        *,
        lane: str,
        limits_in_force: Mapping[str, int],
    ) -> tuple[list[dict[str, CodeValue]], list[dict[str, Any]]]:
        """Per text, ``{signal id: value}`` for every code-rule signal; and per signal the runner's
        report (contract, bundle, host fault)."""
        per_text: list[dict[str, CodeValue]] = [{} for _ in texts]
        reports = []
        for signal in code_signals(document):
            found = stored[signal.id]
            answer = dispatch.run_texts(
                self.client(),
                DocumentRef(signal.code_sha256, found.canonical),
                signal.function,
                signal.params,
                texts,
                limits=limits_in_force,
                lane=lane,
            )
            for i, value in enumerate(answer.values):
                per_text[i][signal.id] = value
            reports.append(
                {
                    "signal": signal.id,
                    "contract_version": answer.contract_version,
                    "bundle_digest": answer.bundle_digest,
                    "host_fault": answer.host_fault,
                }
            )
        return per_text, reports


def not_approved(signal_id: str, sha: str, state: str) -> RuleSetError:
    nexts = {
        "pending": ("dataworks_list_code_rules", "an agent's save of it waits for the operator"),
        "revoked": (
            "dataworks_list_code_rules",
            "the operator revoked it; only the operator approves it again (Code rules screen)",
        ),
    }
    tool, why = nexts.get(
        state, ("dataworks_save_code_rule", "save the code rule and ask the operator")
    )
    return RuleSetError(
        422,
        "code_rule_not_approved",
        f"Signal {signal_id!r} names code rule {sha}, which is {state}; a rule set may only name "
        "an approved code rule.",
        {"signal": signal_id, "code_sha256": sha, "state": state},
        next_tool=tool,
        why=why,
    )


def compile_for(document: RuleSetDocument) -> CompiledRuleSet:
    """The document compiled with code rules admitted (their values come from the runner)."""
    return CompiledRuleSet(document, code_rules=True)


def case_values(
    rules: CodeRules,
    document: RuleSetDocument,
    stored: Mapping[str, Stored],
    *,
    lane: str = "interactive",
) -> dict[str, dict[str, CodeValue]] | None:
    """Per case id, the runner's values of the document's code rules on that case's text, or None
    when the runner could not give every one (a host fault: no verdict on the cases)."""
    from ...rules.cases import case_input

    if not code_signals(document):
        return {c.id: {} for c in document.cases}
    texts = [case_input(c)[0] for c in document.cases]
    values, reports = rules.values(
        document, stored, texts, lane=lane, limits_in_force=limits.in_force()
    )
    if any(r["host_fault"] for r in reports):
        return None
    return {c.id: v for c, v in zip(document.cases, values, strict=True)}


def resolve(
    rules: CodeRules, document: RuleSetDocument
) -> tuple[dict[str, Any], dict[str, Stored]]:
    """The four parameters a build adds for a rule set naming code rules (FTDD §7.5), and the
    stored version per signal. The switch-off build still resolves (rows then read
    ``rule_worker_unavailable``, §8.4) but never contacts the runner here."""
    enabled = get_settings().code_rules_enabled
    stored: dict[str, Stored] = {}
    for signal in code_signals(document):
        if enabled:
            stored[signal.id] = rules.check_signal(signal)
        else:
            stored[signal.id] = _stored_without_switch(rules, signal)
    value = {
        DOCUMENTS: {s.code_sha256: stored[s.id].canonical for s in code_signals(document)},
        VERSIONS: {s.code_sha256: stored[s.id].version_id for s in code_signals(document)},
        APPROVALS: {s.code_sha256: stored[s.id].approve_row_id for s in code_signals(document)},
        LIMITS: limits.in_force(),
    }
    return {PARAM: canonical_json(value).decode("utf-8")}, stored


def _stored_without_switch(rules: CodeRules, signal: CodeRuleSignal) -> Stored:
    """With the switch off, approval still decides (a revoked hash is refused), nothing runs."""
    sha = signal.code_sha256
    row = rules.stored(sha)
    state, approve_row = rules.approval(sha)
    if state != "approved" or approve_row is None or row is None:
        raise not_approved(signal.id, sha, state if state != "approved" else "unknown")
    return Stored(
        version_id=row.id,
        name=row.name,
        number=row.number,
        canonical=str(row.body_canonical),
        document=json.loads(str(row.body_canonical)),
        approve_row_id=approve_row,
    )
