"""Dry runs (FR-018.27; FTDD §7.4): unapproved code, the caller's own texts, nothing stored.

A document inline (with its cases) or a stored ``{name, version}``; its cases and up to
``CODE_RULE_DRY_RUN_MAX_TEXTS`` texts run through the runner in ``dry_run`` mode at the limits in
force, on the interactive lane. Per case and per text, each function's count or reason, and - here
only, on the caller's own text - the bounded diagnostic and the fuel used. Nothing is read from a
dataset and nothing is written: the route takes no database session for a document given inline,
and a stored one is read in its own short read-only session. Logs carry counts, never a text.
"""

from __future__ import annotations

import logging
from typing import Any

from ...clients.runner_client import DocumentRef, RunCall, RunnerRefused
from ...core.config import get_settings
from ...core.database import async_session_factory
from ...rules.next_step import next_for
from . import cases as case_runs
from . import documents, limiter, limits, store
from .errors import CodeRuleError, disabled, runner_unavailable
from .save import capacity_exhausted, refused_by_runner

logger = logging.getLogger(__name__)


async def _stored(name: str, version: int) -> tuple[Any, Any]:
    async with async_session_factory()() as db:
        row = await store.get_version(db, name, version)
        return row.body["document"], row.body["cases"]


async def run(
    *,
    document: Any,
    cases: Any,
    name: str | None,
    version: int | None,
    texts: list[str],
    params: dict[str, Any],
    functions: list[str] | None,
) -> dict[str, Any]:
    if not get_settings().code_rules_enabled:
        raise disabled()
    settings = get_settings()
    if (document is None) == (name is None):
        raise CodeRuleError(
            422,
            "code_rule_invalid",
            "Give either a document (and its cases), or a stored code rule's name and version.",
            {"pointer": "/"},
            next_tool="dataworks_list_code_rules",
            why="find a stored code rule to test",
        )
    if len(texts) > settings.code_rule_dry_run_max_texts:
        raise CodeRuleError(
            413,
            "cap_exceeded",
            f"CODE_RULE_DRY_RUN_MAX_TEXTS allows {settings.code_rule_dry_run_max_texts} texts; "
            f"this dry run has {len(texts)}. Nothing was truncated.",
            {
                "setting": "CODE_RULE_DRY_RUN_MAX_TEXTS",
                "limit": settings.code_rule_dry_run_max_texts,
                "actual": len(texts),
            },
            next_tool="dataworks_get_runner_contract",
            why="read the dry-run limits",
        )
    if name is not None:
        if version is None:
            raise CodeRuleError(
                422,
                "code_rule_invalid",
                "A stored code rule is named by its name AND version.",
                {"pointer": "/version"},
                next_tool="dataworks_get_code_rule",
                why="read the versions of the code rule",
            )
        document, cases = await _stored(name, version)
    parsed = documents.parse(document, cases, require_cases=False)
    chosen = functions if functions is not None else parsed.function_names
    for function in chosen:
        if function not in parsed.function_names:
            raise CodeRuleError(
                422,
                "code_rule_function_missing",
                f"The document does not declare {function}.",
                {"function": function},
                next_tool="dataworks_test_code_rule",
                why="name a declared function",
            )
    if texts:  # the texts' parameters; the cases carry their own (checked by the parse)
        documents.check_params(parsed.document, params, "/params")
    calls = case_runs.calls_for(parsed.cases, 1)
    text_calls = [RunCall(function, params, texts, 1) for function in chosen] if texts else []
    if not calls and not text_calls:
        raise CodeRuleError(
            422,
            "code_rule_cases_missing",
            "Nothing to run: give the document's cases, or texts to run it on.",
            {"pointer": "/cases"},
            next_tool="dataworks_test_code_rule",
            why="try it with its cases or your texts",
        )
    await limiter.take()
    try:
        answer = await limits.client().run(
            DocumentRef(parsed.code_sha256, parsed.canonical),
            calls + text_calls,
            mode="dry_run",
            lane="interactive",
            limits=limits.in_force(),
            send_document=True,
        )
    except RunnerRefused as exc:
        raise refused_by_runner(exc) from None
    if not answer.available:
        raise runner_unavailable("no answer within the retry budget")
    case_out = [v.out() for v in case_runs.verdicts(parsed.cases, answer.results[: len(calls)])]
    text_results = answer.results[len(calls) :]
    text_out = [
        {
            "index": i,
            "results": {fn: text_results[k][i] for k, fn in enumerate(chosen)},
        }
        for i in range(len(texts))
    ]
    all_passed = all(c["passed"] for c in case_out)
    # Some calls found no free runner slot (the per-request lane backstop): say so, never "correct
    # the rule" for what is the runner's capacity (018 Review R2).
    starved = capacity_exhausted(answer.results)
    logger.info(
        "code-rule dry run %s: %d cases (%d passed), %d texts x %d functions",
        parsed.code_sha256[:12],
        len(case_out),
        sum(c["passed"] for c in case_out),
        len(texts),
        len(chosen),
    )
    return {
        "code_sha256": parsed.code_sha256,
        "contract_version": answer.contract_version,
        "bundle_digest": answer.bundle_digest,
        "limits": limits.in_force(),
        "cases": case_out,
        "all_cases_passed": all_passed,
        "texts": text_out,
        "runner_capacity_exhausted": starved,
        "next": (
            next_for(
                "dataworks_health",
                {},
                "the runner had no free slot for some calls (rule_worker_unavailable); try again",
            )
            if starved
            else (
                next_for(
                    "dataworks_save_code_rule",
                    {},
                    "every case passed: save it (an agent's save waits for the operator)",
                )
                if all_passed and parsed.cases
                else next_for(
                    "dataworks_test_code_rule",
                    {},
                    "correct the rule or its cases and try again",
                )
            )
        ),
    }
