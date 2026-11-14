"""``rule_set@1``: run a stored rule set over every row of a build (FR-016.22; FTDD 016 section 6.4).

A recipe step PINS ``rule_set_name``, ``rule_set_version`` and ``rule_set_sha256``; the build
fills ``rule_set_document`` with the stored canonical text (``services/rules/step_resolution.py``).
This operator then:

- refuses to run without the document (``rule_set_document_missing``: an unresolved call path) or
  when the document's sha256 differs from the pin (``rule_set_hash_mismatch``);
- evaluates the document per row with ``src/rules/compose`` (the evaluator every app conforms to);
- writes EXACTLY the declared output columns, each prefixed with ``output_prefix``, plus
  ``<prefix>vetoed`` when the document declares vetoes;
- reports ``{"rule_set_use": {name, version, sha256, behaviour_key, use}}`` on every batch, which
  the build cross-checks against the pin when it records the use (FR-016.24).

A validator that raises fails the step ``rule_signal_error`` naming the signal, the validator and
the row key, never the text; no default verdict is written. ``element_column`` is the element a
``reads: element`` pattern and an ``element`` validator candidate read. A null text is read as the
empty text. Deterministic, no model.

There is no ``before_column`` / ``after_column`` (FTDD 016 section 6.4 lists them): the contract
checks an element candidate with empty neighbours (``compose._validate``), so such a parameter would
change nothing; it is recorded in the controls record rather than shipped as a knob that does nothing.

**Code rules (feature 018 slice 4; FTDD 018 §7.6; FR-018.21, FR-018.22, FR-018.38, FR-018.39).** The
build fills ``code_rule_resolution`` (the approved documents, their versions and approve rows, and
the ``runner_limits`` in force). This operator re-hashes every document
(``code_rule_hash_mismatch``), then per batch and
per code-rule signal asks the runner (``ctx.runner``, ``mode: stored``, ``lane: batch``, the resolved
limits) for one value per row - a count, or a reason - and hands them to ``compose`` (fired iff
count >= 1, no span: ``spans: null``). A failed signal is never "did not fire": the outputs that
depend on it are null, ``<prefix><signal>__reason`` names the reason (null on success), and with
``use`` label, veto or screen the row is DROPPED (``code_rule_failed``, the drop naming the signal and
the reason, never the text). With the switch off or the runner unreachable every code-rule value is
``rule_worker_unavailable`` and the rest of the step goes on (FR-018.22). The report adds
``code_rule_uses`` (per hash: contract, bundle, failed rows, reasons, host fault) and
``code_rule_rows`` (rows, rows with an operator-code reason, rows with a host fault), which the
build's use recorder, failure share and reuse block read (``services/code_rules/uses.py``).
"""

from __future__ import annotations

import functools
import hashlib
import json
from collections.abc import Mapping
from typing import Any

import pyarrow as pa

from ....rules.rule_set import NAME_PATTERN, USES, CodeRuleSignal, RuleSetDocument
from ....runner_contract.reasons import HOST_FAULTS
from ...context import RunContext
from ...manifest import OperatorManifest, ResourceSpec
from ...protocol import OperatorResult
from ..identifiers.common import (
    PREFIX_PATTERN,
    PROVIDER_VERSION,
    append_outputs,
    pairs,
    refuse,
    schema,
    text_values,
)

DEFAULT_PREFIX = "rs_"
#: The largest stored document a build can carry (``RULE_SET_MAX_DOCUMENT_BYTES``' ceiling).
DOCUMENT_MAX_CHARS = 104_857_600
_TYPES = {"bool": pa.bool_(), "int": pa.int64(), "string": pa.string()}

RULE_SET_MANIFEST = OperatorManifest(
    name="rule_set",
    version="1",
    provider="native",
    provider_version=PROVIDER_VERSION,
    kind="labeler",
    scope="row",
    description=(
        "Runs one stored rule set (pinned by name, version and sha256) over a text column and "
        "writes the output columns the rule set declares, prefixed, plus <prefix>vetoed when it "
        "declares vetoes. The build fills in the stored document; a stored sha256 that differs "
        "from the pin is refused. Records which rule set ran and why (use). Deterministic, no "
        "model."
    ),
    output_columns=(),
    params_schema=schema(
        {
            "rule_set_name": {
                "type": "string",
                "pattern": NAME_PATTERN,
                "title": "Rule set",
                "x-hint": "The stored rule set's name (dataworks_list_rule_sets).",
            },
            "rule_set_version": {"type": "integer", "minimum": 1, "title": "Version"},
            "rule_set_sha256": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
                "title": "sha256",
                "x-hint": "The pinned version's sha256 (dataworks_get_rule_set); a stored version "
                "whose sha256 differs is refused.",
            },
            "use": {
                "type": "string",
                "enum": list(USES),
                "title": "Use",
                "x-hint": "Why this build runs the rule set; recorded with the version.",
            },
            "text_column": {"type": "string", "minLength": 1, "title": "Text column"},
            "element_column": {
                "type": "string",
                "minLength": 1,
                "title": "Element column",
                "x-hint": "The element inside the text that element patterns and element "
                "validator candidates read.",
            },
            "output_prefix": {
                "type": "string",
                "pattern": PREFIX_PATTERN,
                "default": DEFAULT_PREFIX,
                "title": "Output prefix",
            },
            "rule_set_document": {
                "type": "string",
                "maxLength": DOCUMENT_MAX_CHARS,
                "title": "Filled in by the build",
                "x-advanced": True,
                "x-hint": "The stored canonical document; a recipe never sets it.",
            },
            # Feature 018: filled in by the build for a rule set naming code rules (never by a
            # recipe): the canonical JSON of the approved documents by sha256, their versions,
            # approve rows and the runner limits in force - all inside the step identity.
            "code_rule_resolution": {
                "type": "string",
                "maxLength": DOCUMENT_MAX_CHARS,
                "title": "Filled in by the build",
                "x-advanced": True,
                "x-hint": "The approved code rules this rule set names; a recipe never sets it.",
            },
        },
        ["rule_set_name", "rule_set_version", "rule_set_sha256", "use", "text_column"],
    ),
    resources=ResourceSpec(queue="curation", cpu_class="light", memory_class="light"),
    deterministic=True,
)


def _compose() -> Any:
    """``rules.compose``, imported at call time: ``rules.validators`` imports the identifier
    schemes, which load this package while the validator registry is still initialising."""
    from ....rules import compose

    return compose


@functools.lru_cache(maxsize=64)
def _compiled(text: str, code_rules: bool = False) -> Any:
    """One compile per stored document text (FTDD 016 section 10.4)."""
    return _compose().CompiledRuleSet(
        RuleSetDocument.model_validate(json.loads(text)), code_rules=code_rules
    )


#: The uses whose rows a failed code rule drops (FR-018.21; FTDD 018 §7.6).
DROPPING_USES = frozenset({"label", "veto", "screen"})
REASON_SUFFIX = "__reason"


def _prefix(params: Mapping[str, Any]) -> str:
    value = params.get("output_prefix")
    return DEFAULT_PREFIX if value is None else str(value)


def declared_columns(document: Mapping[str, Any], prefix: str) -> list[tuple[str, str]]:
    """``(column, type)`` the document makes this step write, in declaration order; then, per
    code-rule signal, its reason column (feature 018)."""
    out = [(prefix + str(o["column"]), str(o["type"])) for o in document.get("outputs", [])]
    if document.get("vetoes"):
        out.append((prefix + "vetoed", "bool"))
    for signal in document.get("signals", []):
        if signal.get("kind") == "code_rule":
            out.append((prefix + str(signal["id"]) + REASON_SUFFIX, "string"))
    return out


class RuleSetOp:
    manifest = RULE_SET_MANIFEST

    def columns_for(self, params: Mapping[str, Any]) -> tuple[list[str], list[str], list[str]]:
        """Reads the text (and element) column; writes the document's declared columns. Without a
        resolved document it answers the manifest's columns (none): an unresolved call path is
        refused by :meth:`run`, never by a crash here."""
        reads = [str(params[k]) for k in ("text_column", "element_column") if params.get(k)]
        text = params.get("rule_set_document")
        if not isinstance(text, str):
            return reads, [c.name for c in self.manifest.output_columns], []
        try:
            document = json.loads(text)
        except ValueError:
            return reads, [c.name for c in self.manifest.output_columns], []
        return reads, [name for name, _ in declared_columns(document, _prefix(params))], []

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        text = params.get("rule_set_document")
        name, version = str(params["rule_set_name"]), int(params["rule_set_version"])
        if not isinstance(text, str):
            raise refuse(
                "rule_set_document_missing",
                f"rule_set@1 ran without the stored document of {name}@{version}: the build "
                "resolves it from the pin before dispatch.",
                {"name": name, "version": version},
            )
        found = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if found != params["rule_set_sha256"]:
            raise refuse(
                "rule_set_hash_mismatch",
                f"The document handed to rule_set@1 hashes to {found}, not the pinned "
                f"{params['rule_set_sha256']} of {name}@{version}.",
                {"name": name, "version": version, "pinned": params["rule_set_sha256"]},
            )
        code_docs = _code_documents(params, text, name, version)
        compiled = _compiled(text, bool(code_docs))
        document = compiled.document
        prefix = _prefix(params)
        texts = text_values(batch, str(params["text_column"]), "text_column")
        elements: list[str | None] = (
            text_values(batch, str(params["element_column"]), "element_column")
            if params.get("element_column")
            else [None] * batch.num_rows
        )
        keys = pairs(batch)
        columns = declared_columns(document.model_dump(by_alias=True), prefix)
        values: dict[str, list[Any]] = {c: [] for c, _ in columns}
        vetoed_column = prefix + "vetoed" if document.vetoes else None
        code_values, code_report = _code_values(document, params, code_docs, texts, ctx)
        use = str(params["use"])
        events = []
        keep: list[int] = []
        failed_rows = guest_rows = host_rows = 0
        for row, (row_text, element) in enumerate(zip(texts, elements, strict=True)):
            code = {sid: values[row] for sid, values in code_values.items()} if code_docs else None
            try:
                result = compiled.evaluate(row_text or "", element, code)
            except _compose().RuleSignalError as exc:
                raise refuse(
                    "rule_signal_error",
                    f"Signal {exc.signal_id!r} ({exc.validator}) of {name}@{version} raised "
                    f"{exc.error_type} on row {keys[row][0]}; no verdict was written.",
                    {
                        "signal": exc.signal_id,
                        "validator": exc.validator,
                        "row_key": keys[row][0],
                        "error": exc.error_type,
                    },
                ) from None
            if result.failed:
                failed_rows += 1
                guest_rows += any(r not in HOST_FAULTS for r in result.failed.values())
                host_rows += any(r in HOST_FAULTS for r in result.failed.values())
            if result.failed_outputs and use in DROPPING_USES:
                # FR-018.21: the used output is null because operator code failed: the row goes,
                # and the drop log names the signal and the reason, never the text.
                why = "; ".join(f"{sid}: {reason}" for sid, reason in sorted(result.failed.items()))
                events.append(
                    ctx.drop(
                        keys[row],
                        "code_rule_failed",
                        why,
                        "code_rule_failed",
                        text=",".join(sorted(set(result.failed.values()))),
                    )
                )
                continue
            keep.append(row)
            for out in document.outputs:
                values[prefix + out.column].append(result.outputs[out.column])
            if vetoed_column is not None:
                values[vetoed_column].append(result.vetoed)
            for signal_id in code_values:
                values[prefix + signal_id + REASON_SUFFIX].append(result.failed.get(signal_id))
        kept = batch if len(keep) == batch.num_rows else batch.take(pa.array(keep, pa.int64()))
        output = append_outputs(
            kept,
            [(c, pa.array(values[c], _TYPES[t])) for c, t in columns],
            self.manifest.ref_text,
        )
        report: dict[str, Any] = {
            "rule_set_use": {
                "name": name,
                "version": version,
                "sha256": found,
                "behaviour_key": document.behaviour_key,
                "use": use,
            }
        }
        if code_docs:
            report["code_rule_uses"] = code_report
            report["code_rule_rows"] = {
                "rows": batch.num_rows,
                "failed": failed_rows,
                "operator_code_failed": guest_rows,
                "host_fault": host_rows,
            }
        return OperatorResult(
            output=output,
            events=events,
            output_roles=dict.fromkeys(values, "metadata"),
            report=report,
        )


def _code_documents(
    params: Mapping[str, Any], text: str, name: str, version: int
) -> dict[str, str]:
    """``{sha: canonical text}`` of the document's code rules, each re-hashed (feature 018).
    Empty for a rule set without code rules."""
    signals = [s for s in json.loads(text).get("signals", []) if s.get("kind") == "code_rule"]
    if not signals:
        return {}
    raw = params.get("code_rule_resolution")
    docs = json.loads(raw).get("documents") if isinstance(raw, str) else None
    if not isinstance(docs, Mapping) or any(s["code_sha256"] not in docs for s in signals):
        raise refuse(
            "code_rule_documents_missing",
            f"rule_set@1 ran {name}@{version}, which names code rules, without their stored "
            "documents: the build resolves them (approval, contract) before dispatch.",
            {"name": name, "version": version},
        )
    out: dict[str, str] = {}
    for signal in signals:
        sha = str(signal["code_sha256"])
        body = str(docs[sha])
        found = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if found != sha:
            raise refuse(
                "code_rule_hash_mismatch",
                f"The code-rule document handed to rule_set@1 for signal {signal['id']!r} hashes "
                f"to {found}, not {sha}.",
                {"signal": signal["id"], "pinned": sha, "found": found},
            )
        out[sha] = body
    return out


def _code_values(
    document: RuleSetDocument,
    params: Mapping[str, Any],
    docs: Mapping[str, str],
    texts: list[str | None],
    ctx: RunContext,
) -> tuple[dict[str, list[Any]], list[dict[str, Any]]]:
    """Per code-rule signal, one value per row from the runner, and the per-hash report."""
    if not docs:
        return {}, []
    from ....clients.runner_client import DocumentRef
    from ....core.config import get_settings
    from ....core.errors import AppError
    from ....services.code_rules import dispatch

    client = None
    fault: dict[str, Any] | None = None
    if not get_settings().code_rules_enabled:
        fault = {"code": "code_rules_disabled", "setting": "CODE_RULES_ENABLED"}
    else:
        try:
            client = ctx.runner
        except AppError as exc:  # RUNNER_URL unset: the runner is unavailable, not a failure
            fault = {"code": "runner_unavailable", "detail": exc.code}
    raw = params.get("code_rule_resolution")
    resolved = json.loads(raw) if isinstance(raw, str) else {}
    limits = {k: int(v) for k, v in dict(resolved.get("runner_limits") or {}).items()}
    values: dict[str, list[Any]] = {}
    by_sha: dict[str, dict[str, Any]] = {}
    for signal in document.signals:
        if not isinstance(signal, CodeRuleSignal):
            continue
        answer = dispatch.run_texts(
            client,
            DocumentRef(signal.code_sha256, docs[signal.code_sha256]),
            signal.function,
            signal.params,
            [t or "" for t in texts],
            limits=limits,
            lane="batch",
        )
        values[signal.id] = answer.values
        entry = by_sha.setdefault(
            signal.code_sha256,
            {
                "code_sha256": signal.code_sha256,
                "runner_contract": signal.runner_contract,
                "contract_version": None,
                "bundle_digest": None,
                "failed_rows": 0,
                "reasons": {},
                "host_fault": None,
            },
        )
        entry["contract_version"] = entry["contract_version"] or answer.contract_version
        entry["bundle_digest"] = entry["bundle_digest"] or answer.bundle_digest
        entry["host_fault"] = entry["host_fault"] or fault or answer.host_fault
    for sha, entry in by_sha.items():
        failed = [
            [values[s.id][row] for s in document.signals if getattr(s, "code_sha256", None) == sha]
            for row in range(len(texts))
        ]
        for row_values in failed:
            reasons = sorted({v for v in row_values if isinstance(v, str)})
            if reasons:
                entry["failed_rows"] += 1
                for reason in reasons:
                    entry["reasons"][reason] = entry["reasons"].get(reason, 0) + 1
    return values, list(by_sha.values())
