"""``midataworks.rule-set/v1``: the rule-set document (FR-016.10; FTDD 016 section 4.10).

``RuleSetDocument`` is the ONE source (ADR-017): it validates every document, and
``docs/schemas/midataworks-rule-set-v1.json`` is generated from it by
``backend/scripts/write_rule_set_schema.py`` (the only writer; ``test_rule_set_schema_sync.py``
compares the bytes). Every model forbids unknown keys; semantic options (``ignore_case``,
``fold_fullwidth_ascii``, ``word_boundary``, ``unit``, ``side``, ``counting``) have NO default, so an
agent states ``ignore_case: false``. A document that nests a rule set fails this schema (Q12 (a)).

**Identity (D38-4, operator 2026-10-23):** ``sha256(canonical_bytes(document))``, where
``canonical_bytes`` is **RFC 8785** (JSON Canonicalization Scheme, the ``rfc8785`` package; the scheme
miStudio's probe identity uses) over the document AS RECEIVED: the parsed JSON values, before any
default is applied, never a re-dump of a model. So key order never changes the digest (keys sort by
UTF-16 code units), numbers are values written in ECMAScript form (``1`` and ``1.0`` are the same
number; ``1e21`` is written ``1e+21``), strings are UTF-8 with only ``"``, ``\\`` and U+0000-U+001F
escaped, and an ABSENT list and an EMPTY list are different documents (``vetoes: []`` is part of the
document; an omitted ``vetoes`` is not). An integer beyond +/-(2^53 - 1) has no exact RFC 8785 form,
so a document carrying one is refused (``unsafe_integer``, naming its JSON Pointer). This is NOT
``core.canonical_json`` (ADR-005), which keeps hashing recipes, steps and rows unchanged; the
rule-set identity is the one cross-app digest, and the ``rule_set_identity`` cases of
``rule-signal-cases-v1.json`` pin its bytes for every app.

**Unicode (Q31 (a)):** every string must already be NFC; the first one that is not is refused as
``not_nfc`` naming its JSON Pointer. Nothing is normalised silently.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

import rfc8785
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema
from pydantic_core import PydanticCustomError

from ..schemas.behaviour import BEHAVIOUR_KEY_PATTERN

FORMAT = "midataworks.rule-set/v1"
LISTING_FORMAT = "midataworks.rule-sets/v1"
NAME_PATTERN = r"^[a-z0-9][a-z0-9-]{0,63}$"
ID_PATTERN = r"^[a-z][a-z0-9_]{0,47}$"
CASE_ID_PATTERN = r"^[a-z0-9][a-z0-9_.-]{0,95}$"
SLUG_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,47}$"
POINTER_PATTERN = r"^(/([^~/]|~[01])*)*$"
MAX_EXPRESSION_DEPTH = 8
#: RFC 8785 writes numbers as IEEE-754 doubles: an integer beyond +/-(2^53 - 1) has no exact form.
SAFE_INTEGER = 2**53 - 1
#: The step parameter ``use`` of ``rule_set@1`` (FR-016.24): a purpose, not a document field.
USES = ("label", "veto", "screen", "decoy_check", "conflict_scan")

SCHEMA_FILE_NAME = "midataworks-rule-set-v1.json"
SCHEMA_ID = (
    "https://raw.githubusercontent.com/hitsainet/miDataworks/main/docs/schemas/" + SCHEMA_FILE_NAME
)
#: The copy the image ships (the image is built from ``backend/`` and has no ``docs/``).
PACKAGED_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "data" / SCHEMA_FILE_NAME
REGENERATE_COMMAND = "cd backend && python -m scripts.write_rule_set_schema"

SignalId = Annotated[str, Field(pattern=ID_PATTERN)]
ExprName = Annotated[str, Field(pattern=ID_PATTERN)]
Column = Annotated[str, Field(pattern=ID_PATTERN)]
Text200 = Annotated[str, Field(min_length=1, max_length=200)]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --- signals ------------------------------------------------------------------------------------


class PatternSignal(_M):
    """Every non-overlapping match of ``pattern``, left to right; each match is a span."""

    id: SignalId
    kind: Literal["pattern"]
    pattern: str = Field(min_length=1, max_length=2000)
    ignore_case: bool
    fold_fullwidth_ascii: bool
    reads: Literal["text", "element"] = Field(
        description="text: the row's text; element: the element column (spans are then offsets "
        "in the element)."
    )


class ProximitySignal(_M):
    """For each span of ``anchor``, the nearest listed phrase on ``side``; yields the anchor span
    when the edge-to-edge gap is at most ``max_distance`` ``unit``s."""

    id: SignalId
    kind: Literal["proximity"]
    anchor: SignalId
    phrases: list[Text200] = Field(min_length=1, max_length=512)
    word_boundary: bool
    max_distance: int = Field(ge=0)
    unit: Literal["code_points", "utf8_bytes"]
    side: Literal["before", "after", "either"]
    ignore_case: bool
    fold_fullwidth_ascii: bool


class ValidatorRef(_M):
    id: str = Field(pattern=ID_PATTERN)
    version: int = Field(ge=1)


class ScanCandidates(_M):
    scan: Literal[True]


class SpanOfCandidates(_M):
    span_of: SignalId


class ElementCandidates(_M):
    element: Literal[True]


class ValidatorSignal(_M):
    """Candidates from the validator's all-candidates scan, from each span of another signal, or
    from the element column; fires on a candidate only when ``valid`` is true."""

    id: SignalId
    kind: Literal["validator"]
    validator: ValidatorRef
    params: dict[str, Any] = Field(description="Per the validator's params_schema.")
    candidates: ScanCandidates | SpanOfCandidates | ElementCandidates


class Term(_M):
    term: Text200
    weight: float = Field(allow_inf_nan=False)


class LexicalWeightsSignal(_M):
    """Fires when the summed weight of matched terms is at least ``cut``."""

    id: SignalId
    kind: Literal["lexical_weights"]
    terms: list[Term] = Field(min_length=1)
    cut: float = Field(allow_inf_nan=False)
    counting: Literal["presence", "occurrences"]
    ignore_case: bool
    fold_fullwidth_ascii: bool
    word_boundary: bool


class CodeRuleOutput(_M):
    name: Column
    type: Literal["bool", "int", "string"]


class CodeRuleSignal(_M):
    """Declared by reference only; save refuses it until feature 018 (``code_rule_not_available``)."""

    id: SignalId
    kind: Literal["code_rule"]
    code_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runner_contract: str = Field(min_length=1, max_length=200)
    function: str = Field(pattern=r"^[a-z_][a-z0-9_]{0,63}$")
    params: dict[str, Any]
    outputs: list[CodeRuleOutput] = Field(min_length=1)


Signal = Annotated[
    PatternSignal | ProximitySignal | ValidatorSignal | LexicalWeightsSignal | CodeRuleSignal,
    Field(discriminator="kind"),
]


# --- expressions --------------------------------------------------------------------------------


class SignalExpr(_M):
    signal: SignalId


class AllExpr(_M):
    all: list[Expr] = Field(min_length=1)


class AnyExpr(_M):
    any: list[Expr] = Field(min_length=1)


class NotExpr(_M):
    not_: Expr = Field(alias="not")


class WithinN(_M):
    a: Expr
    b: Expr
    n: int = Field(ge=0)
    unit: Literal["code_points", "utf8_bytes"]


class WithinNExpr(_M):
    within_n: WithinN


Expr = SignalExpr | AllExpr | AnyExpr | NotExpr | WithinNExpr


# --- outputs, vetoes, renderings, decoys, substitutions ----------------------------------------


class FiredValue(_M):
    fired: ExprName


class CountValue(_M):
    count: ExprName


class FirstValue(_M):
    first: ExprName


class SpansValue(_M):
    spans: ExprName


class FieldValue(_M):
    field: tuple[SignalId, Annotated[str, Field(pattern=ID_PATTERN)]]


OutputValue = FiredValue | CountValue | FirstValue | SpansValue | FieldValue
_VALUE_TYPE = {"fired": "bool", "count": "int", "first": "string", "spans": "string"}


class Output(_M):
    column: Column
    type: Literal["bool", "int", "string"]
    value: OutputValue


class Veto(_M):
    id: SignalId
    when: ExprName
    removes: list[SignalId] = Field(min_length=1)


class RenderValidator(_M):
    validator: dict[str, Any]


class RenderMapping(_M):
    mapping: dict[str, str] = Field(min_length=1)


class RenderBuiltin(_M):
    builtin: Literal["spaced", "split_lines", "spelled_out_digits"]


class NormaliseValidator(_M):
    validator: ValidatorRef


class NormaliseMappingInverse(_M):
    mapping_inverse: Literal[True]


class NormaliseBuiltin(_M):
    #: ``words_to_digits`` (D38-3, appended within v1) is ``spelled_out_digits``' inverse.
    builtin: Literal["digits_only", "strip_delimiters", "words_to_digits"]


class Rendering(_M):
    rendering: str = Field(pattern=SLUG_PATTERN)
    render: RenderValidator | RenderMapping | RenderBuiltin
    normaliser: NormaliseValidator | NormaliseMappingInverse | NormaliseBuiltin
    invisible_to: list[SignalId]


class RuleSetDecoyRef(_M):
    name: str = Field(pattern=NAME_PATTERN)
    version: int = Field(ge=1)
    decoy_id: SignalId


class GenerateValidator(_M):
    validator: dict[str, Any]


class GenerateRuleSet(_M):
    rule_set: RuleSetDecoyRef


class GenerateList(_M):
    items: list[str] = Field(alias="list", min_length=1, max_length=1000)


class Decoy(_M):
    id: SignalId
    description: str = Field(min_length=1, max_length=2000)
    must_not_fire: list[Column] = Field(min_length=1)
    generator: GenerateValidator | GenerateRuleSet | GenerateList


class Substitution(_M):
    id: SignalId
    pointer: str = Field(pattern=POINTER_PATTERN)
    description: str = Field(min_length=1, max_length=2000)


# --- cases --------------------------------------------------------------------------------------


class TextInput(_M):
    text: str


class ValueInput(_M):
    """The element inserted into text: the row's text is ``before + value + after`` and its
    element is ``value``."""

    value: str
    before: str = ""
    after: str = ""


class Expect(_M):
    outputs: dict[str, StrictBool | StrictInt | StrictStr | None]
    vetoed: bool
    spans: dict[str, list[tuple[int, int]]] | None = None


class CaseSource(_M):
    kind: Literal["published_test_value", "published_reference_value", "unissuable", "constructed"]
    citation: str | None = None
    retrieved: date | None = None


class Case(_M):
    id: str = Field(pattern=CASE_ID_PATTERN)
    input: TextInput | ValueInput
    expect: Expect
    source: CaseSource
    note: str


class Provenance(_M):
    kind: Literal["derived"]
    derivation_id: str = Field(min_length=1, max_length=64)
    method: str = Field(min_length=1, max_length=64)
    method_version: int = Field(ge=1)
    source_version_id: str = Field(min_length=1, max_length=64)
    label_column: str = Field(min_length=1, max_length=256)
    seed: int
    params_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


# --- the document -------------------------------------------------------------------------------


def _walk_strings(value: Any, pointer: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield pointer, value
    elif isinstance(value, dict):
        for key, item in value.items():
            esc = str(key).replace("~", "~0").replace("/", "~1")
            yield pointer + "/" + esc, str(key)
            yield from _walk_strings(item, pointer + "/" + esc)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from _walk_strings(item, f"{pointer}/{i}")


def _walk_integers(value: Any, pointer: str = "") -> Iterator[tuple[str, int]]:
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        yield pointer, value
    elif isinstance(value, dict):
        for key, item in value.items():
            esc = str(key).replace("~", "~0").replace("/", "~1")
            yield from _walk_integers(item, pointer + "/" + esc)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from _walk_integers(item, f"{pointer}/{i}")


def first_unsafe_integer(document: Any) -> str | None:
    """The JSON Pointer of the first integer outside +/-(2^53 - 1), else None (D38-4)."""
    for pointer, number in _walk_integers(document):
        if abs(number) > SAFE_INTEGER:
            return pointer
    return None


def first_non_nfc(document: Any) -> str | None:
    """The JSON Pointer of the first string (key or value) that is not NFC, else None."""
    for pointer, text in _walk_strings(document):
        if not unicodedata.is_normalized("NFC", text):
            return pointer
    return None


def _depth(expr: Expr) -> int:
    if isinstance(expr, SignalExpr):
        return 1
    if isinstance(expr, AllExpr):
        return 1 + max(_depth(e) for e in expr.all)
    if isinstance(expr, AnyExpr):
        return 1 + max(_depth(e) for e in expr.any)
    if isinstance(expr, NotExpr):
        return 1 + _depth(expr.not_)
    return 1 + max(_depth(expr.within_n.a), _depth(expr.within_n.b))


def signals_named(expr: Expr) -> list[str]:
    if isinstance(expr, SignalExpr):
        return [expr.signal]
    if isinstance(expr, AllExpr):
        return [s for e in expr.all for s in signals_named(e)]
    if isinstance(expr, AnyExpr):
        return [s for e in expr.any for s in signals_named(e)]
    if isinstance(expr, NotExpr):
        return signals_named(expr.not_)
    return signals_named(expr.within_n.a) + signals_named(expr.within_n.b)


def _unique(items: list[str], what: str) -> None:
    seen: set[str] = set()
    for item in items:
        if item in seen:
            raise ValueError(f"{what} {item!r} is repeated")
        seen.add(item)


class RuleSetDocument(_M):
    """A rule set: signals, named expressions over them, outputs, vetoes and the cases it must pass."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        title="miDataworks Rule Set v1",
    )

    format: Literal["midataworks.rule-set/v1"]
    name: str = Field(pattern=NAME_PATTERN)
    behaviour_key: str = Field(pattern=BEHAVIOUR_KEY_PATTERN)
    description: str = Field(min_length=1, max_length=2000)
    signals: list[Signal] = Field(min_length=1)
    expressions: dict[ExprName, Expr] = Field(min_length=1)
    outputs: list[Output] = Field(min_length=1, max_length=64)
    vetoes: list[Veto] = Field(default_factory=list)
    renderings: list[Rendering] = Field(default_factory=list)
    decoys: list[Decoy] = Field(default_factory=list)
    substitutions: list[Substitution] = Field(default_factory=list)
    cases: list[Case]
    provenance: Provenance | SkipJsonSchema[None] = None

    @model_validator(mode="before")
    @classmethod
    def _nfc_and_no_null(cls, data: Any) -> Any:
        if isinstance(data, dict):
            pointer = first_non_nfc(data)
            if pointer is not None:
                raise PydanticCustomError(
                    "not_nfc",
                    "the string at {pointer} is not Unicode NFC; normalise it before saving",
                    {"pointer": pointer},
                )
            pointer = first_unsafe_integer(data)
            if pointer is not None:
                raise PydanticCustomError(
                    "unsafe_integer",
                    "the integer at {pointer} is outside +/-(2^53 - 1); RFC 8785 (the rule-set "
                    "identity) cannot write it exactly",
                    {"pointer": pointer},
                )
            if "provenance" in data and data["provenance"] is None:
                raise ValueError("provenance is absent or an object, never null")
        return data

    @model_validator(mode="after")
    def _references(self) -> RuleSetDocument:
        ids = [s.id for s in self.signals]
        _unique(ids, "signal id")
        earlier: set[str] = set()
        for signal in self.signals:
            ref = None
            if isinstance(signal, ProximitySignal):
                ref = signal.anchor
            elif isinstance(signal, ValidatorSignal) and isinstance(
                signal.candidates, SpanOfCandidates
            ):
                ref = signal.candidates.span_of
            if ref is not None and ref not in earlier:
                raise ValueError(
                    f"signal {signal.id!r} refers to {ref!r}, which is not an earlier signal"
                )
            if isinstance(signal, LexicalWeightsSignal):
                _unique([t.term for t in signal.terms], f"term of {signal.id!r}")
            earlier.add(signal.id)
        for name, expr in self.expressions.items():
            if _depth(expr) > MAX_EXPRESSION_DEPTH:
                raise ValueError(f"expression {name!r} nests deeper than {MAX_EXPRESSION_DEPTH}")
            for s in signals_named(expr):
                if s not in earlier:
                    raise ValueError(f"expression {name!r} names an unknown signal {s!r}")
        columns = [o.column for o in self.outputs]
        _unique(columns, "output column")
        for out in self.outputs:
            key = next(iter(out.value.model_dump(by_alias=True)))
            if key == "field":
                sig, _ = out.value.field  # type: ignore[union-attr]
                if sig not in earlier:
                    raise ValueError(f"output {out.column!r} reads an unknown signal {sig!r}")
            else:
                ref_name = getattr(out.value, key)
                if ref_name not in self.expressions:
                    raise ValueError(f"output {out.column!r} names an unknown expression")
                if _VALUE_TYPE[key] != out.type:
                    raise ValueError(
                        f"output {out.column!r}: {key} is a {_VALUE_TYPE[key]}, not {out.type}"
                    )
        _unique([v.id for v in self.vetoes], "veto id")
        for veto in self.vetoes:
            if veto.when not in self.expressions:
                raise ValueError(f"veto {veto.id!r} names an unknown expression {veto.when!r}")
            for s in veto.removes:
                if s not in earlier:
                    raise ValueError(f"veto {veto.id!r} removes an unknown signal {s!r}")
        _unique([r.rendering for r in self.renderings], "rendering")
        for rendering in self.renderings:
            for s in rendering.invisible_to:
                if s not in earlier:
                    raise ValueError(f"rendering {rendering.rendering!r}: unknown signal {s!r}")
        _unique([d.id for d in self.decoys], "decoy id")
        for decoy in self.decoys:
            for c in decoy.must_not_fire:
                if c not in columns:
                    raise ValueError(f"decoy {decoy.id!r}: unknown output column {c!r}")
        _unique([s.id for s in self.substitutions], "substitution id")
        _unique([c.id for c in self.cases], "case id")
        for case in self.cases:
            if set(case.expect.outputs) != set(columns):
                raise ValueError(f"case {case.id!r} must state every output column: {columns}")
            for name in case.expect.spans or {}:
                if name not in self.expressions:
                    raise ValueError(f"case {case.id!r} states spans of unknown {name!r}")
        return self


AllExpr.model_rebuild()
AnyExpr.model_rebuild()
NotExpr.model_rebuild()
WithinN.model_rebuild()
WithinNExpr.model_rebuild()
RuleSetDocument.model_rebuild()


class RuleSetListed(_M):
    name: str = Field(pattern=NAME_PATTERN)
    version: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    behaviour_key: str = Field(pattern=BEHAVIOUR_KEY_PATTERN)
    use: Literal["label", "veto", "screen", "decoy_check", "conflict_scan"]
    path: str = Field(pattern=r"^rule_sets/[a-z0-9][a-z0-9-]{0,63}@[1-9][0-9]*\.json$")


class RuleSetListing(_M):
    """``extensions["midataworks.rule-sets/v1"]`` of a dataset-version manifest (slice 3)."""

    recorded: Literal[True]
    rule_sets: list[RuleSetListed]


def canonical_bytes(document: dict[str, Any]) -> bytes:
    """RFC 8785 (JCS) bytes of the document as received: the exact bytes the identity hashes."""
    return rfc8785.dumps(document)


def digest(document: dict[str, Any]) -> str:
    """The rule-set identity: lower-case hex sha256 of :func:`canonical_bytes` (D38-4)."""
    return hashlib.sha256(canonical_bytes(document)).hexdigest()


def parse(document: dict[str, Any]) -> RuleSetDocument:
    return RuleSetDocument.model_validate(document)


SCHEMA_COMMENT = (
    "Semantics every app implements (feature 016; the cases in rule-signal-cases-v1.json pin each "
    "rule). UNITS: every span and offset is in Unicode code points; spans are half-open [start, end); "
    "a signal that reads the element has spans in the element. PATTERN DIALECT: RE2 syntax on a "
    "subset, RE2 default options: case-sensitive unless ignore_case (Unicode simple case folding: k "
    "matches U+212A); '.' does not match newline; ^ and $ match only at the start and end of the text "
    "($ never before a final newline); \\d \\w \\s \\b are ASCII and \\s is [\\t\\n\\f\\r ] (no \\v). "
    "Refused, naming the rule and the code-point position of the construct: backreference, "
    "lookaround, conditional, atomic, possessive, inline_flag, unicode_property, any_byte (\\C), "
    "repeat_too_large (a count over 1000, or nested counted repeats multiplying past 1000), "
    "too_long (over 2000 code points; position 2000), syntax (also: escapes outside \\d \\D \\s \\S "
    "\\w \\W \\b \\B \\A \\z \\a \\f \\t \\n \\r \\v \\0 \\xHH and escaped ASCII punctuation; a "
    "quantified anchor or boundary; {,n}; (?<name>; repeated group names; '[' inside a class; a class "
    "escape or reversed range as a range endpoint). NORMALISATION: text is matched as given; only "
    "declared options apply: ignore_case, and fold_fullwidth_ascii (U+FF01-U+FF5E to U+0021-U+007E, "
    "U+3000 to U+0020, one to one, applied before matching; substrings come from the original text). "
    "SIGNALS: pattern = every non-overlapping match left to right (after a match ending at e the "
    "search resumes at e, one code point later when the match was empty). proximity = for each anchor span, "
    "the nearest phrase occurrence (literal; word_boundary = ASCII \\b on both sides; each phrase "
    "matched on its own, so phrases may overlap each other) on the declared side, ignoring a phrase "
    "that overlaps the anchor; gap edge to edge (phrase end to anchor start, or anchor end to phrase "
    "start) in the declared unit (utf8_bytes = UTF-8 length of the ORIGINAL text between the two "
    "spans); ties: smaller gap, then earlier phrase in phrases, then before over after; yields the "
    "anchor span with outputs distance, phrase (as written in the text) and side when gap <= "
    "max_distance. validator = candidates from the validator's scan, from each span of an earlier "
    "signal (value = the span's text, neighbouring text on each side), or from the element; each "
    "candidate carries the validator's outputs; the signal FIRES on a candidate only when valid is "
    "true, invalid candidates stay readable by field outputs. lexical_weights = one match pass over "
    "all terms (longest term first at a position; word_boundary wraps them in ASCII \\b); score = sum "
    "of the weights of matched terms in text order, each term once under presence and once per match "
    "under occurrences; fires when score >= cut; its spans are the first match of each distinct "
    "matched term, so count is the number of distinct terms. code_rule = declared only (feature 018). "
    "A signal may refer (anchor, span_of) only to an earlier signal. EVALUATION of one row: (1) every "
    "signal's spans; (2) vetoes per candidate: each veto's 'when' expression is evaluated on the "
    "step-1 spans, then every span of each signal in 'removes' that overlaps a 'when' span "
    "(half-open: s1 < e2 and s2 < e1) is removed; vetoed is true when any span was removed; (3) "
    "expressions on the remaining spans: signal = true when it has a span, carrying its spans; all = "
    "true when every member is true, carrying every member's spans; any = true when some member is "
    "true, carrying the true members' spans; not = true when its member is false; within_n(a, b, n, "
    "unit) = true when some pair of spans of a and b has gap <= n (edge to edge; overlap is 0), "
    "carrying the contributing spans; a false expression and a not carry no spans; carried spans are "
    "sorted by start then end without duplicates; (4) outputs: fired = the expression's truth; count "
    "= number of carried spans; first = text of the first carried span, else null; spans = canonical "
    "JSON [[start, end], ...]; field = the named output of the first remaining candidate of a signal, "
    "else null. RENDERINGS, DECOYS, SUBSTITUTIONS are declared and structurally checked here; "
    "feature 017 executes renderings and decoys; substitutions are clone parameters (JSON "
    "Pointers). RENDERING SEMANTICS (every rendering has normalise(render(v)) == v cases): render "
    "builtin spaced = the value's characters joined by one space; split_lines = joined by one "
    "newline (U+000A); spelled_out_digits = each ASCII digit 0-9 written as its lower-case English "
    "word (zero one two three four five six seven eight nine), every other character kept as "
    "itself, the resulting tokens joined by one space; mapping = left to right, at each position "
    "the LONGEST key that matches there is replaced by its value, a character no key matches is "
    "kept. Normaliser builtin digits_only = keep the ASCII digits 0-9 only; strip_delimiters = "
    "remove exactly U+0020 space, U+000A newline, '-' and '.'; words_to_digits = the inverse of "
    "spelled_out_digits: read the text as tokens separated by exactly one space, a token that is a "
    "digit word becomes its digit and any other token is one kept character; mapping_inverse = "
    "the mapping inverted (its values must be distinct), applied the same way (longest key "
    "first). IDENTITY: sha256 of the RFC 8785 (JCS) canonical JSON of the document as "
    "received (the parsed JSON values before any default; never a re-dump of a model): keys sorted "
    "by UTF-16 code units, no whitespace, numbers as values in ECMAScript form (1 and 1.0 are the "
    "same number, 1e21 is 1e+21), strings UTF-8 with only the quote, backslash and U+0000-U+001F "
    "escaped. An ABSENT list and an EMPTY list differ: an omitted vetoes and vetoes: [] are "
    "different documents with different digests. An integer outside +/-(2^53 - 1) is refused "
    "(unsafe_integer, naming its JSON Pointer). Strings must be NFC. BOUNDS (settings, refused naming the setting, never "
    "truncated): RULE_SET_MAX_SIGNALS, RULE_SET_MAX_CASES, RULE_SET_MAX_DOCUMENT_BYTES, "
    "RULE_SET_MAX_TERMS (lexical terms, default 2000), RULE_SET_MAX_CODE_PARAMS_BYTES (code_rule "
    "params, default 16 KiB)."
)


def schema_document() -> dict[str, Any]:
    generated = RuleSetDocument.model_json_schema(by_alias=True)
    listing = RuleSetListing.model_json_schema(by_alias=True)
    defs = dict(generated.pop("$defs", {}))
    defs.update(listing.pop("$defs", {}))
    defs["RuleSetListing"] = listing
    defs["RuleSetUse"] = {
        "description": "The rule_set@1 step parameter 'use': why a build ran the rule set.",
        "enum": list(USES),
        "type": "string",
    }
    generated.pop("title", None)
    generated.pop("description", None)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "miDataworks Rule Set v1",
        "description": (
            "A rule set (format midataworks.rule-set/v1). GENERATED from "
            "backend/src/rules/rule_set.py (RuleSetDocument) by "
            "backend/scripts/write_rule_set_schema.py; do not edit by hand. Consumers vendor this "
            "file byte-identical. Changes within v1 are additive only."
        ),
        "$comment": SCHEMA_COMMENT,
        **generated,
        "$defs": defs,
    }


def render_schema_file() -> bytes:
    """The exact bytes of ``docs/schemas/midataworks-rule-set-v1.json``."""
    return (
        json.dumps(schema_document(), indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode("ascii")
