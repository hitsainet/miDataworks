"""Evaluate a rule set on one row (FTDD 016 sections 5.3, 5.5; FTID section 3.3).

Steps, each a small function so a mutation control can target one line:

1. :meth:`CompiledRuleSet._signal_spans` - every signal's spans, computed ONCE per row into a dict
   (the operation-count guard, FTDD 016 section 10.3: each pattern signal's matcher runs once per row
   and a validator's ``check`` once per candidate; expressions only read the dict);
2. :func:`_apply_vetoes` - per candidate, half-open overlap;
3. :meth:`CompiledRuleSet._expr` - ``signal``, ``all``, ``any``, ``not``, ``within_n``;
4. :meth:`CompiledRuleSet._outputs` - ``fired``, ``count``, ``first``, ``spans``, ``field``.

The semantics are stated in words in ``midataworks-rule-set-v1.json``'s ``$comment`` and pinned by
``rule-signal-cases-v1.json``. This module is pure (FR-016.4).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import jsonschema

from ..core.canonical_json import canonical_json
from . import dialect
from .rule_set import (
    AllExpr,
    AnyExpr,
    CodeRuleSignal,
    ElementCandidates,
    Expr,
    FieldValue,
    LexicalWeightsSignal,
    NotExpr,
    PatternSignal,
    ProximitySignal,
    RuleSetDocument,
    SignalExpr,
    SpanOfCandidates,
    ValidatorSignal,
    WithinNExpr,
)
from .validators import resolve

TEXT = "text"
ELEMENT = "element"


class RuleSetNotRunnable(ValueError):
    """The document cannot be evaluated here: ``code_rule_not_available``, ``validator_unknown``,
    ``validator_params_invalid``, ``pattern_refused`` or ``field_unknown``."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


class RuleSignalError(RuntimeError):
    """A validator raised while a row was evaluated (FTDD 016 section 8.4). Carries the signal and
    validator, NEVER the text: callers turn it into ``rule_signal_error`` without a default verdict.
    """

    def __init__(self, signal_id: str, validator: str, error: BaseException) -> None:
        super().__init__(f"signal {signal_id!r} ({validator}) raised {type(error).__name__}")
        self.signal_id = signal_id
        self.validator = validator
        self.error_type = type(error).__name__


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    source: str = TEXT
    outputs: Mapping[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, int, int]:
        return (self.source, self.start, self.end)


@dataclass
class SignalResult:
    #: Spans the signal fires on (for a validator: the valid candidates).
    spans: list[Span]
    #: Every candidate (a validator's invalid ones too); equals ``spans`` for other kinds.
    candidates: list[Span]


@dataclass
class RowResult:
    signals: dict[str, dict[str, Any]]
    vetoed: bool
    vetoes: dict[str, int]
    outputs: dict[str, Any]
    expressions: dict[str, dict[str, Any]]
    deciding: dict[str, dict[str, Any]]


def overlaps(a: Span, b: Span) -> bool:
    """Half-open ``[s, e)`` overlap, in the same string."""
    return a.source == b.source and a.start < b.end and b.start < a.end


def _sorted_unique(spans: list[Span]) -> list[Span]:
    seen: dict[tuple[str, int, int], Span] = {}
    for s in spans:
        seen.setdefault(s.key, s)
    return sorted(seen.values(), key=lambda s: (s.start, s.end, s.source))


def _gap(a: Span, b: Span, unit: str, strings: Mapping[str, str]) -> int | None:
    """Edge-to-edge gap of two spans of the same string; overlap is 0; None across strings."""
    if a.source != b.source:
        return None
    if a.start < b.end and b.start < a.end:
        return 0
    lo, hi = (a.end, b.start) if a.end <= b.start else (b.end, a.start)
    if unit == "utf8_bytes":
        return len(strings[a.source][lo:hi].encode("utf-8"))
    return hi - lo


def _apply_vetoes(
    results: dict[str, SignalResult], vetoes: list[tuple[str, list[Span], list[str]]]
) -> dict[str, int]:
    """Remove, from each signal a veto names, every span (and candidate) overlapping one of the
    veto's spans; count the removed FIRING spans per veto (``vetoed`` is any count above 0)."""
    removed: dict[str, int] = {}
    for veto_id, when_spans, removes in vetoes:
        count = 0
        for signal_id in removes:
            result = results[signal_id]
            keep = [s for s in result.spans if not any(overlaps(s, w) for w in when_spans)]
            keep_all = [s for s in result.candidates if not any(overlaps(s, w) for w in when_spans)]
            count += len(result.spans) - len(keep)
            result.spans, result.candidates = keep, keep_all
        removed[veto_id] = count
    return removed


class CompiledRuleSet:
    """A validated document compiled once: patterns, validators and their params checked."""

    def __init__(self, document: RuleSetDocument) -> None:
        self.document = document
        self._compiled: dict[str, Any] = {}
        self._phrases: dict[str, list[Any]] = {}
        self._lexical: dict[str, tuple[Any, list[tuple[str, float]]]] = {}
        self._validators: dict[str, Any] = {}
        for signal in document.signals:
            self._compile_signal(signal)
        self._check_fields()

    # --- compile ------------------------------------------------------------------------------

    def _pattern(self, signal_id: str, pattern: str, ignore_case: bool) -> Any:
        try:
            return dialect.compile_pattern(pattern, ignore_case)
        except dialect.PatternRefused as exc:
            raise RuleSetNotRunnable(
                "pattern_refused",
                f"signal {signal_id!r}: {exc}",
                {"signal": signal_id, "rule": exc.rule, "position": exc.position},
            ) from None

    def _compile_signal(self, signal: Any) -> None:
        if isinstance(signal, PatternSignal):
            self._compiled[signal.id] = self._pattern(signal.id, signal.pattern, signal.ignore_case)
        elif isinstance(signal, ProximitySignal):
            self._phrases[signal.id] = [
                self._pattern(signal.id, self._literal(p, signal.word_boundary), signal.ignore_case)
                for p in signal.phrases
            ]
        elif isinstance(signal, LexicalWeightsSignal):
            ordered = sorted(enumerate(signal.terms), key=lambda it: (-len(it[1].term), it[0]))
            alternation = "|".join(f"({dialect.escape_literal(t.term)})" for _, t in ordered)
            body = f"(?:{alternation})"
            pattern = rf"\b{body}\b" if signal.word_boundary else body
            self._lexical[signal.id] = (
                self._pattern(signal.id, pattern, signal.ignore_case),
                [(t.term, float(t.weight)) for _, t in ordered],
            )
        elif isinstance(signal, ValidatorSignal):
            try:
                validator = resolve(signal.validator.id, signal.validator.version)
            except LookupError:
                raise RuleSetNotRunnable(
                    "validator_unknown",
                    f"signal {signal.id!r}: validator {signal.validator.id}@"
                    f"{signal.validator.version} is not registered",
                    {
                        "signal": signal.id,
                        "validator": signal.validator.id,
                        "version": signal.validator.version,
                    },
                ) from None
            errors = sorted(
                jsonschema.Draft202012Validator(validator.decl.params_schema).iter_errors(
                    signal.params
                ),
                key=str,
            )
            if errors:
                raise RuleSetNotRunnable(
                    "validator_params_invalid",
                    f"signal {signal.id!r}: {errors[0].message}",
                    {"signal": signal.id, "validator": validator.decl.ref},
                )
            self._validators[signal.id] = validator
        elif isinstance(signal, CodeRuleSignal):
            raise RuleSetNotRunnable(
                "code_rule_not_available",
                f"signal {signal.id!r}: code rules arrive with feature 018",
                {"signal": signal.id},
            )

    @staticmethod
    def _literal(text: str, word_boundary: bool) -> str:
        body = dialect.escape_literal(text)
        return rf"\b{body}\b" if word_boundary else body

    def _field_names(self, signal_id: str) -> set[str]:
        signal = next(s for s in self.document.signals if s.id == signal_id)
        if isinstance(signal, ValidatorSignal):
            return {name for name, _ in self._validators[signal_id].decl.outputs}
        if isinstance(signal, ProximitySignal):
            return {"distance", "phrase", "side"}
        if isinstance(signal, LexicalWeightsSignal):
            return {"term", "weight"}
        return {"text"}

    def _check_fields(self) -> None:
        for out in self.document.outputs:
            if isinstance(out.value, FieldValue):
                sig, name = out.value.field
                if name not in self._field_names(sig):
                    raise RuleSetNotRunnable(
                        "field_unknown",
                        f"output {out.column!r}: signal {sig!r} has no output {name!r}",
                        {"column": out.column, "signal": sig, "field": name},
                    )

    # --- step 1: signals ----------------------------------------------------------------------

    def _signal_spans(self, text: str, element: str | None) -> dict[str, SignalResult]:
        strings = {TEXT: text, ELEMENT: element or ""}
        folded: dict[tuple[str, bool], str] = {}

        def view(source: str, fold: bool) -> str:
            key = (source, fold)
            if key not in folded:
                raw = strings[source]
                folded[key] = dialect.fold_fullwidth_ascii(raw) if fold else raw
            return folded[key]

        results: dict[str, SignalResult] = {}
        for signal in self.document.signals:
            if isinstance(signal, PatternSignal):
                source = ELEMENT if signal.reads == "element" else TEXT
                if source == ELEMENT and element is None:
                    spans: list[Span] = []
                else:
                    raw = strings[source]
                    spans = [
                        Span(m.start(), m.end(), source, {"text": raw[m.start() : m.end()]})
                        for m in dialect.finditer(
                            self._compiled[signal.id], view(source, signal.fold_fullwidth_ascii)
                        )
                    ]
                results[signal.id] = SignalResult(spans, spans)
            elif isinstance(signal, ProximitySignal):
                spans = self._proximity(signal, results[signal.anchor].spans, text, view)
                results[signal.id] = SignalResult(spans, spans)
            elif isinstance(signal, ValidatorSignal):
                try:
                    candidates = self._validate(signal, results, text, element)
                except Exception as exc:  # noqa: BLE001 - re-raised named, without the text
                    raise RuleSignalError(
                        signal.id, f"{signal.validator.id}@{signal.validator.version}", exc
                    ) from None
                results[signal.id] = SignalResult(
                    [c for c in candidates if c.outputs.get("valid") is True], candidates
                )
            elif isinstance(signal, LexicalWeightsSignal):
                spans = self._lexical_spans(signal, text, view)
                results[signal.id] = SignalResult(spans, spans)
        return results

    def _proximity(
        self, signal: ProximitySignal, anchors: list[Span], text: str, view: Any
    ) -> list[Span]:
        haystack = view(TEXT, signal.fold_fullwidth_ascii)
        occurrences: list[tuple[int, int, int]] = []  # (start, end, phrase order)
        for order, compiled in enumerate(self._phrases[signal.id]):
            occurrences.extend(
                (m.start(), m.end(), order) for m in dialect.finditer(compiled, haystack)
            )
        out: list[Span] = []
        for anchor in anchors:
            if anchor.source != TEXT:
                continue
            best: tuple[int, int, int, int, int] | None = None
            for s, e, order in occurrences:
                if e <= anchor.start and signal.side in ("before", "either"):
                    gap, side = self._units(text, e, anchor.start, signal.unit), 0
                elif s >= anchor.end and signal.side in ("after", "either"):
                    gap, side = self._units(text, anchor.end, s, signal.unit), 1
                else:
                    continue
                candidate = (gap, order, side, s, e)
                if best is None or candidate[:3] < best[:3]:
                    best = candidate
            if best is not None and best[0] <= signal.max_distance:
                gap, _, side, s, e = best
                out.append(
                    Span(
                        anchor.start,
                        anchor.end,
                        TEXT,
                        {
                            "distance": gap,
                            "phrase": text[s:e],
                            "side": "before" if side == 0 else "after",
                        },
                    )
                )
        return out

    @staticmethod
    def _units(text: str, lo: int, hi: int, unit: str) -> int:
        if unit == "utf8_bytes":
            return len(text[lo:hi].encode("utf-8"))
        return hi - lo

    def _validate(
        self,
        signal: ValidatorSignal,
        results: dict[str, SignalResult],
        text: str,
        element: str | None,
    ) -> list[Span]:
        validator = self._validators[signal.id]
        names = [name for name, _ in validator.decl.outputs]

        def span(start: int, end: int, source: str, verdict: Any) -> Span:
            return Span(start, end, source, {n: getattr(verdict, n) for n in names})

        candidates = signal.candidates
        if isinstance(candidates, SpanOfCandidates):
            return [
                span(
                    s.start,
                    s.end,
                    s.source,
                    validator.check(
                        (text if s.source == TEXT else element or "")[s.start : s.end],
                        (text if s.source == TEXT else element or "")[: s.start],
                        (text if s.source == TEXT else element or "")[s.end :],
                        signal.params,
                    ),
                )
                for s in results[candidates.span_of].spans
            ]
        if isinstance(candidates, ElementCandidates):
            if element is None:
                return []
            return [span(0, len(element), ELEMENT, validator.check(element, "", "", signal.params))]
        return [span(c.start, c.end, TEXT, c.verdict) for c in validator.scan(text, signal.params)]

    def _lexical_spans(self, signal: LexicalWeightsSignal, text: str, view: Any) -> list[Span]:
        compiled, terms = self._lexical[signal.id]
        score = 0.0
        first: dict[int, Span] = {}
        for m in dialect.finditer(compiled, view(TEXT, signal.fold_fullwidth_ascii)):
            index = next(i for i, g in enumerate(m.groups()) if g is not None)
            term, weight = terms[index]
            if signal.counting == "occurrences" or index not in first:
                score += weight
            if index not in first:
                first[index] = Span(m.start(), m.end(), TEXT, {"term": term, "weight": weight})
        if score >= signal.cut:
            return sorted(first.values(), key=lambda s: (s.start, s.end))
        return []

    def signal_results(self, text: str, element: str | None = None) -> dict[str, SignalResult]:
        """Step 1 alone: every signal's spans and candidates before any veto."""
        return self._signal_spans(text, element)

    # --- step 3: expressions ------------------------------------------------------------------

    def _expr(
        self, expr: Expr, results: dict[str, SignalResult], strings: Mapping[str, str]
    ) -> tuple[bool, list[Span]]:
        if isinstance(expr, SignalExpr):
            spans = results[expr.signal].spans
            return bool(spans), list(spans)
        if isinstance(expr, AllExpr):
            members = [self._expr(e, results, strings) for e in expr.all]
            if not all(t for t, _ in members):
                return False, []
            return True, _sorted_unique([s for _, ss in members for s in ss])
        if isinstance(expr, AnyExpr):
            members = [self._expr(e, results, strings) for e in expr.any]
            if not any(t for t, _ in members):
                return False, []
            return True, _sorted_unique([s for t, ss in members if t for s in ss])
        if isinstance(expr, NotExpr):
            truth, _ = self._expr(expr.not_, results, strings)
            return not truth, []
        assert isinstance(expr, WithinNExpr)
        body = expr.within_n
        _, a_spans = self._expr(body.a, results, strings)
        _, b_spans = self._expr(body.b, results, strings)
        hits: list[Span] = []
        for a in a_spans:
            for b in b_spans:
                gap = _gap(a, b, body.unit, strings)
                if gap is not None and gap <= body.n:
                    hits.extend((a, b))
        return bool(hits), _sorted_unique(hits)

    # --- the row ------------------------------------------------------------------------------

    def evaluate(self, text: str, element: str | None = None) -> RowResult:
        strings = {TEXT: text, ELEMENT: element or ""}
        results = self._signal_spans(text, element)
        before_veto = {
            k: SignalResult(list(v.spans), list(v.candidates)) for k, v in results.items()
        }
        vetoes = [
            (
                v.id,
                self._expr(self.document.expressions[v.when], before_veto, strings)[1],
                v.removes,
            )
            for v in self.document.vetoes
        ]
        removed = _apply_vetoes(results, vetoes)
        evaluated = {
            name: self._expr(expr, results, strings)
            for name, expr in self.document.expressions.items()
        }
        outputs, deciding = self._outputs(results, evaluated, strings, removed)
        return RowResult(
            signals={
                sid: {
                    "fired": bool(r.spans),
                    "spans": [[s.start, s.end] for s in r.spans],
                    "substrings": [strings[s.source][s.start : s.end] for s in r.spans],
                    "outputs": [dict(s.outputs) for s in r.candidates],
                }
                for sid, r in results.items()
            },
            vetoed=any(removed.values()),
            vetoes=removed,
            outputs=outputs,
            expressions={
                name: {"fired": truth, "spans": [[s.start, s.end] for s in spans]}
                for name, (truth, spans) in evaluated.items()
            },
            deciding=deciding,
        )

    def _outputs(
        self,
        results: dict[str, SignalResult],
        evaluated: dict[str, tuple[bool, list[Span]]],
        strings: Mapping[str, str],
        removed: dict[str, int],
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        outputs: dict[str, Any] = {}
        deciding: dict[str, dict[str, Any]] = {}
        vetoes_applied = sorted(k for k, v in removed.items() if v)
        for out in self.document.outputs:
            value = out.value
            if isinstance(value, FieldValue):
                sig, name = value.field
                remaining = sorted(results[sig].candidates, key=lambda s: (s.start, s.end))
                outputs[out.column] = remaining[0].outputs.get(name) if remaining else None
                deciding[out.column] = {"signal": sig, "field": name, "vetoes": vetoes_applied}
                continue
            key = next(iter(value.model_dump(by_alias=True)))
            name = getattr(value, key)
            truth, spans = evaluated[name]
            if key == "fired":
                outputs[out.column] = truth
            elif key == "count":
                outputs[out.column] = len(spans)
            elif key == "first":
                outputs[out.column] = (
                    strings[spans[0].source][spans[0].start : spans[0].end] if spans else None
                )
            else:
                outputs[out.column] = canonical_json([[s.start, s.end] for s in spans]).decode()
            deciding[out.column] = {"expression": name, "fired": truth, "vetoes": vetoes_applied}
        return outputs, deciding


def compile_rule_set(document: RuleSetDocument) -> CompiledRuleSet:
    return CompiledRuleSet(document)


def evaluate(document: RuleSetDocument, text: str, element: str | None = None) -> RowResult:
    """One row's result (FTDD 016 section 5.5)."""
    return CompiledRuleSet(document).evaluate(text, element)
