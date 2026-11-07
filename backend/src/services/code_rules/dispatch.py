"""Running one code-rule function over many texts through the runner (feature 018 slice 4).

Used wherever a rule set's code-rule signal needs values: ``rule_set@1`` in a build (``lane:
batch``), a rule set's own cases at save and at build load, and a rule-set preview (``lane:
interactive``). Each text gets a :data:`~src.rules.compose.CodeValue`: its count, or the reason it
has none. Two rules from 018 Review R2 live here:

- **D-R2-1, the lane backstop.** The runner bounds a request's lane wait by ONE deadline, so the
  tail of a large request can read ``rule_worker_unavailable`` while its head ran. Those starved
  texts are sent again, in a new request, while each round computes at least one text; a round that
  computes nothing stops (the runner is down or full), and what is left reads
  ``rule_worker_unavailable`` - a host fault, which never fails a step and blocks the step's reuse
  (decision I7), so a starved row is never cached as the rule's answer.
- **D-R2-2, a refused limit is named.** The runner refusing a limit this deployment sends
  (``limit_above_ceiling``, a ``CODE_RULE_*`` setting past its ``RUNNER_MAX_*``) leaves every row
  ``rule_worker_unavailable`` (D36 (2): a deployment fault never fails a step) and the answer's
  ``host_fault`` names the limit and the setting, which the step's report carries - it never reads
  as "the runner cannot be reached".

Nothing here logs a text, a source or a parameter (FR-018.25).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ...clients.runner_client import DocumentRef, RunCall, RunnerClient, RunnerRefused
from ...runner_contract.reasons import Reason
from .save import LIMIT_NAME, LIMIT_REFUSALS, LIMIT_SETTINGS

logger = logging.getLogger(__name__)

#: Texts per request: the runner's default ``RUNNER_BATCH_TEXTS`` (its body bound admits twice it).
BATCH_TEXTS = 256
UNAVAILABLE = Reason.RULE_WORKER_UNAVAILABLE.value


@dataclass
class Dispatched:
    """The value per text, and what the runner reported (``None`` when it never answered)."""

    values: list[int | str]
    contract_version: str | None = None
    bundle_digest: str | None = None
    #: Why some or every text reads ``rule_worker_unavailable`` (a host fault), else None.
    host_fault: dict[str, Any] | None = None
    requests: int = 0
    rounds: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


def _refusal_fault(exc: RunnerRefused) -> dict[str, Any]:
    if exc.code in LIMIT_REFUSALS:
        found = LIMIT_NAME.search(exc.message)
        limit = found.group(1) if found else None
        return {
            "code": "runner_limit_refused",
            "runner_code": exc.code,
            "limit": limit,
            "setting": LIMIT_SETTINGS.get(limit or ""),
        }
    return {"code": "runner_refused", "runner_code": exc.code}


def run_texts(
    client: RunnerClient | None,
    document: DocumentRef,
    function: str,
    params: Mapping[str, Any],
    texts: Sequence[str],
    *,
    limits: Mapping[str, int],
    lane: str,
    mode: str = "stored",
    batch_texts: int | None = None,
) -> Dispatched:
    """One value per text (see the module docstring). ``client`` None: no runner configured."""
    out = Dispatched(values=[UNAVAILABLE] * len(texts))
    if not texts:
        return out
    if client is None:
        out.host_fault = {"code": "runner_unavailable", "detail": "RUNNER_URL is not configured"}
        return out
    size = max(1, batch_texts if batch_texts is not None else BATCH_TEXTS)
    done: list[bool] = [False] * len(texts)
    pending = list(range(len(texts)))
    while pending:
        out.rounds += 1
        progress = False
        for start in range(0, len(pending), size):
            chunk = pending[start : start + size]
            out.requests += 1
            try:
                answer = client.run(
                    document,
                    [RunCall(function, params, [texts[i] for i in chunk])],
                    mode=mode,
                    lane=lane,
                    limits=limits,
                )
            except RunnerRefused as exc:
                out.host_fault = _refusal_fault(exc)
                logger.warning(
                    "code rule %s: the runner refused the request (%s)",
                    document.code_sha256[:12],
                    exc.code,
                )
                return out
            if not answer.available:
                out.host_fault = {"code": "runner_unavailable", "detail": "no answer"}
                return out
            out.contract_version = answer.contract_version or out.contract_version
            out.bundle_digest = answer.bundle_digest or out.bundle_digest
            for i, result in zip(chunk, answer.results[0], strict=True):
                if "count" in result and type(result["count"]) is int:
                    out.values[i], done[i] = int(result["count"]), True
                    progress = True
                elif result.get("reason") == UNAVAILABLE:
                    continue  # starved by the lane backstop: sent again next round
                else:
                    out.values[i], done[i] = (
                        str(result.get("reason") or "rule_output_invalid"),
                        True,
                    )
                    progress = True
        pending = [i for i in pending if not done[i]]
        if not progress:
            break
    if pending:
        out.host_fault = out.host_fault or {
            "code": "runner_capacity_exhausted",
            "rows": len(pending),
        }
    return out
