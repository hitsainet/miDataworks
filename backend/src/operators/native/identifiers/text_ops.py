"""``regex_match@1``, ``phrase_proximity@1`` and ``rule_label@1`` (e2e run gaps G0, G3; 2026-10-21).

Every identifier dictionary's High tier is "pattern + phrase within N bytes", and the docs ship regex
samples. In the e2e run an 11-phrase proximity test was a loop over ``[phrase, window]`` pairs in
Jinja and a tier was three nested ``if``s, because Data Designer allows no ``sum``, ``set`` or
nesting past depth 10. These are deterministic labelers (no model): they add columns only.

``regex_match`` refuses what RE2-style engines (and the documented pattern dialect) do not
offer: backreferences, lookaround, conditionals, atomic groups and possessive quantifiers, a
repeat count over 1000, and a quantifier nested inside another unbounded quantifier (the shape
behind catastrophic backtracking, which Python's ``re`` cannot time out).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

import pyarrow as pa

from ....core.canonical_json import canonical_json
from ...context import RunContext
from ...manifest import ColumnSpec
from ...protocol import OperatorResult
from .common import (
    PREFIX_PATTERN,
    append_outputs,
    column_values,
    manifest,
    output_names,
    refuse,
    text_values,
)

# --------------------------------------------------------------------------------------------
# regex_match
# --------------------------------------------------------------------------------------------

MAX_PATTERN_CHARS = 2000
MAX_REPEAT = 1000
REGEX_OUTPUTS = ("matched", "count", "first", "start", "end", "spans")


def check_pattern(pattern: str, ignore_case: bool = False) -> re.Pattern[str]:
    """Compile ``pattern`` after refusing every construct outside the RE2-like subset, naming the
    rule (``backreference``, ``lookaround``, ``conditional``, ``atomic``, ``possessive``,
    ``repeat_too_large``, ``nested_quantifier``, ``too_long``, ``syntax``)."""
    import re._constants as c  # type: ignore[import-not-found]  # Python 3.11+: opcodes
    import re._parser as parser  # type: ignore[import-not-found]

    if len(pattern) > MAX_PATTERN_CHARS:
        raise _bad(pattern, "too_long", f"at most {MAX_PATTERN_CHARS} characters")
    try:
        tree = parser.parse(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as exc:
        raise _bad(pattern, "syntax", str(exc)) from None
    unbounded = c.MAXREPEAT

    def walk(items: Any, inside_unbounded: bool) -> None:
        for op, arg in items:
            if op in (c.GROUPREF, c.GROUPREF_IGNORE):
                raise _bad(pattern, "backreference", "backreferences (\\1, (?P=name)) are refused")
            if op is c.GROUPREF_EXISTS:
                raise _bad(pattern, "conditional", "conditionals (?(1)...) are refused")
            if op in (c.ASSERT, c.ASSERT_NOT):
                raise _bad(pattern, "lookaround", "lookahead and lookbehind are refused")
            if op is c.ATOMIC_GROUP:
                raise _bad(pattern, "atomic", "atomic groups (?>...) are refused")
            if op is c.POSSESSIVE_REPEAT:
                raise _bad(pattern, "possessive", "possessive quantifiers (*+, ++) are refused")
            if op in (c.MAX_REPEAT, c.MIN_REPEAT):
                low, high, sub = arg
                if (high is not unbounded and high > MAX_REPEAT) or low > MAX_REPEAT:
                    raise _bad(
                        pattern, "repeat_too_large", f"a repeat count is at most {MAX_REPEAT}"
                    )
                if inside_unbounded and high != low:
                    raise _bad(
                        pattern,
                        "nested_quantifier",
                        "a variable quantifier inside an unbounded repeat can backtrack "
                        "catastrophically; rewrite it without the nesting",
                    )
                walk(sub, inside_unbounded or high is unbounded)
            elif op is c.SUBPATTERN:
                walk(arg[-1], inside_unbounded)
            elif op is c.BRANCH:
                for branch in arg[1]:
                    walk(branch, inside_unbounded)

    walk(tree, False)
    try:
        return re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as exc:  # pragma: no cover - parse() already accepted it
        raise _bad(pattern, "syntax", str(exc)) from None


def _bad(pattern: str, rule: str, message: str) -> Exception:
    return refuse(
        "params_invalid",
        f"The pattern is outside the supported (RE2-like) subset - {rule}: {message}.",
        {"param": "pattern", "rule": rule, "pattern": pattern[:200]},
    )


REGEX_MANIFEST = manifest(
    "regex_match",
    "labeler",
    "Matches a regular expression in a text column and records whether it matched, how often, "
    "the first match and its character span, and every span (JSON [[start, end], ...]). The "
    "pattern must stay inside an RE2-like subset (no backreferences, lookaround, conditionals, "
    "atomic groups, possessive quantifiers, repeat counts over 1000 or nested unbounded "
    "quantifiers); a refused pattern names its rule. Deterministic, no model.",
    {
        "column": {"type": "string", "minLength": 1, "title": "Column"},
        "pattern": {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_PATTERN_CHARS,
            "title": "Pattern",
        },
        "ignore_case": {"type": "boolean", "default": False, "title": "Ignore case"},
        "max_spans": {
            "type": "integer",
            "minimum": 1,
            "maximum": 1000,
            "default": 100,
            "title": "Spans kept per row",
        },
        "output_prefix": {
            "type": "string",
            "pattern": PREFIX_PATTERN,
            "default": "rx_",
            "title": "Output prefix",
        },
    },
    ["column", "pattern"],
    output_columns=(
        ColumnSpec(name="rx_matched", type="bool", role="metadata"),
        ColumnSpec(name="rx_count", type="int64", role="metadata"),
        ColumnSpec(name="rx_first", type="string", role="metadata", required=False),
        ColumnSpec(name="rx_start", type="int64", role="metadata", required=False),
        ColumnSpec(name="rx_end", type="int64", role="metadata", required=False),
        ColumnSpec(name="rx_spans", type="string", role="metadata"),
    ),
)


class RegexMatch:
    manifest = REGEX_MANIFEST

    def columns_for(self, params: Mapping[str, Any]) -> tuple[list[str], list[str], list[str]]:
        return [str(params["column"])], output_names(params, "rx_", REGEX_OUTPUTS), []

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        compiled = check_pattern(str(params["pattern"]), bool(params.get("ignore_case", False)))
        keep = int(params.get("max_spans") or 100)
        cols: dict[str, list[Any]] = {k: [] for k in REGEX_OUTPUTS}
        for text in text_values(batch, str(params["column"])):
            spans = [] if text is None else [(m.start(), m.end()) for m in compiled.finditer(text)]
            cols["matched"].append(bool(spans))
            cols["count"].append(len(spans))
            cols["first"].append(text[spans[0][0] : spans[0][1]] if spans and text else None)
            cols["start"].append(spans[0][0] if spans else None)
            cols["end"].append(spans[0][1] if spans else None)
            cols["spans"].append(canonical_json([list(s) for s in spans[:keep]]).decode("utf-8"))
        names = output_names(params, "rx_", REGEX_OUTPUTS)
        types = {"matched": pa.bool_(), "count": pa.int64(), "start": pa.int64(), "end": pa.int64()}
        output = append_outputs(
            batch,
            [
                (name, pa.array(cols[key], types.get(key, pa.string())))
                for name, key in zip(names, REGEX_OUTPUTS, strict=True)
            ],
            self.manifest.ref_text,
        )
        return OperatorResult(output=output, output_roles=dict.fromkeys(names, "metadata"))


# --------------------------------------------------------------------------------------------
# phrase_proximity
# --------------------------------------------------------------------------------------------

PROXIMITY_OUTPUTS = ("distance", "phrase", "near", "side")

PROXIMITY_MANIFEST = manifest(
    "phrase_proximity",
    "labeler",
    "Finds the phrase from a list that sits nearest to an anchor in a text column and records the "
    "gap in bytes (UTF-8) between them, the phrase as written in the text, which side it is on, "
    "and whether the gap is within max_distance_bytes. The anchor is a span (anchor_start_column / "
    "anchor_end_column, e.g. checksum_validate's cv_start and cv_end) or the first occurrence of "
    "anchor_column's value. A phrase overlapping the anchor does not count. key_phrases adds "
    'per-row phrases chosen by key_column ("AD=Andorra": an IBAN\'s own country name). '
    "Deterministic, no model.",
    {
        "column": {"type": "string", "minLength": 1, "title": "Text column"},
        "phrases": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 200},
            "minItems": 1,
            "maxItems": 500,
            "title": "Phrases",
        },
        "max_distance_bytes": {
            "type": "integer",
            "minimum": 0,
            "maximum": 10000,
            "default": 100,
            "title": "Within",
            "x-unit": "bytes",
        },
        "case_sensitive": {"type": "boolean", "default": False, "title": "Case-sensitive"},
        "anchor_start_column": {"type": "string", "minLength": 1, "title": "Anchor start column"},
        "anchor_end_column": {"type": "string", "minLength": 1, "title": "Anchor end column"},
        "anchor_column": {
            "type": "string",
            "minLength": 1,
            "title": "Anchor value column",
            "x-hint": "Instead of a span: the anchor is this value's first occurrence in the text.",
        },
        "key_column": {"type": "string", "minLength": 1, "title": "Key column"},
        "key_phrases": {
            "type": "array",
            "items": {"type": "string", "minLength": 3, "maxLength": 220},
            "maxItems": 2000,
            "title": "Per-key phrases",
            "x-hint": "KEY=phrase entries; a row whose key_column equals KEY also looks for phrase.",
        },
        "output_prefix": {
            "type": "string",
            "pattern": PREFIX_PATTERN,
            "default": "pp_",
            "title": "Output prefix",
        },
    },
    ["column", "phrases"],
    output_columns=(
        ColumnSpec(name="pp_distance", type="int64", role="metadata", required=False),
        ColumnSpec(name="pp_phrase", type="string", role="metadata", required=False),
        ColumnSpec(name="pp_near", type="bool", role="metadata"),
        ColumnSpec(name="pp_side", type="string", role="metadata", required=False),
    ),
)


def _key_map(entries: Sequence[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for entry in entries:
        key, sep, phrase = str(entry).partition("=")
        if not sep or not key or not phrase:
            raise refuse(
                "params_invalid",
                f"key_phrases entry {entry!r} is not KEY=phrase.",
                {"param": "key_phrases"},
            )
        out.setdefault(key, []).append(phrase)
    return out


def nearest(
    text: str,
    start: int,
    end: int,
    phrases: Sequence[str],
    case_sensitive: bool,
) -> tuple[int, str, str] | None:
    """``(gap in UTF-8 bytes, phrase as written, side)`` of the nearest phrase occurrence that
    ends at or before ``start`` or begins at or after ``end``; None when there is none. Ties go to
    the earlier phrase in the list, then the left side."""
    flags = 0 if case_sensitive else re.IGNORECASE
    best: tuple[int, int, int, str, str] | None = None
    for order, phrase in enumerate(phrases):
        for m in re.finditer(re.escape(phrase), text, flags):
            s, e = m.start(), m.end()
            if e <= start:
                gap, side = len(text[e:start].encode("utf-8")), "before"
            elif s >= end:
                gap, side = len(text[end:s].encode("utf-8")), "after"
            else:
                continue
            candidate = (gap, order, 0 if side == "before" else 1, text[s:e], side)
            if best is None or candidate[:3] < best[:3]:
                best = candidate
    if best is None:
        return None
    return best[0], best[3], best[4]


class PhraseProximity:
    manifest = PROXIMITY_MANIFEST

    def columns_for(self, params: Mapping[str, Any]) -> tuple[list[str], list[str], list[str]]:
        reads = [
            str(params[k])
            for k in (
                "column",
                "anchor_start_column",
                "anchor_end_column",
                "anchor_column",
                "key_column",
            )
            if params.get(k)
        ]
        return reads, output_names(params, "pp_", PROXIMITY_OUTPUTS), []

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        span = params.get("anchor_start_column"), params.get("anchor_end_column")
        by_value = params.get("anchor_column")
        if bool(span[0]) != bool(span[1]):
            raise refuse(
                "params_invalid",
                "Give both anchor_start_column and anchor_end_column, or anchor_column instead.",
            )
        if bool(span[0]) == bool(by_value):
            raise refuse(
                "params_invalid",
                "Name the anchor once: anchor_start_column + anchor_end_column (a span), or "
                "anchor_column (a value found in the text).",
            )
        if bool(params.get("key_column")) != bool(params.get("key_phrases")):
            raise refuse("params_invalid", "key_column and key_phrases go together.")
        limit = int(
            params.get("max_distance_bytes", 100)
            if params.get("max_distance_bytes") is not None
            else 100
        )
        case_sensitive = bool(params.get("case_sensitive", False))
        phrases = [str(p) for p in params["phrases"]]
        by_key = _key_map(params.get("key_phrases") or [])
        texts = text_values(batch, str(params["column"]))
        n = batch.num_rows
        if span[0]:
            starts = column_values(batch, str(span[0]), "anchor_start_column")
            ends = column_values(batch, str(span[1]), "anchor_end_column")
            for v in [*starts, *ends]:
                if v is not None and (isinstance(v, bool) or not isinstance(v, int)):
                    raise refuse(
                        "column_not_integer",
                        "The anchor span columns must hold integer character offsets.",
                        {"found": type(v).__name__},
                    )
            anchors = [
                None if a is None or b is None else (int(a), int(b))
                for a, b in zip(starts, ends, strict=True)
            ]
        else:
            values = text_values(batch, str(by_value), "anchor_column")
            anchors = []
            for text, value in zip(texts, values, strict=True):
                at = -1 if text is None or not value else text.find(value)
                anchors.append(None if at < 0 else (at, at + len(value or "")))
        keys = (
            text_values(batch, str(params["key_column"]), "key_column")
            if params.get("key_column")
            else [None] * n
        )
        cols: dict[str, list[Any]] = {k: [] for k in PROXIMITY_OUTPUTS}
        for text, anchor, key in zip(texts, anchors, keys, strict=True):
            found = None
            if text is not None and anchor is not None:
                found = nearest(
                    text, anchor[0], anchor[1], phrases + by_key.get(key or "", []), case_sensitive
                )
            cols["distance"].append(found[0] if found else None)
            cols["phrase"].append(found[1] if found else None)
            cols["near"].append(bool(found and found[0] <= limit))
            cols["side"].append(found[2] if found else None)
        names = output_names(params, "pp_", PROXIMITY_OUTPUTS)
        types = {"distance": pa.int64(), "near": pa.bool_()}
        output = append_outputs(
            batch,
            [
                (name, pa.array(cols[key], types.get(key, pa.string())))
                for name, key in zip(names, PROXIMITY_OUTPUTS, strict=True)
            ],
            self.manifest.ref_text,
        )
        return OperatorResult(output=output, output_roles=dict.fromkeys(names, "metadata"))


# --------------------------------------------------------------------------------------------
# rule_label
# --------------------------------------------------------------------------------------------

_TOKEN = re.compile(
    r"\s*(?:(?P<str>'[^']*'|\"[^\"]*\")|(?P<list>\[[^\]]*\])|(?P<op>==|!=|<=|>=|<|>)"
    r"|(?P<word>[A-Za-z0-9_.@-]+))"
)
_OPS = ("==", "!=", "<=", ">=", "<", ">", "in", "not in", "is null", "is not null")


def _literal(text: str) -> Any:
    if text.startswith(("'", '"')):
        return text[1:-1]
    low = text.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low == "null":
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def _list(text: str) -> list[Any]:
    inner = text[1:-1].strip()
    if not inner:
        return []
    return [_literal(part.strip()) for part in inner.split(",")]


def parse_rule(rule: str) -> tuple[str, list[tuple[str, str, Any]]]:
    """``"<value> if <col> <op> <literal> [and ...]"`` -> (value, conditions). ``<value>`` may be
    ``@column`` (copy that column). Ops: ``== != < <= > >= in [..] not in [..] is null is not
    null``. Literals: true, false, null, numbers, 'quoted' or bare words."""
    head, sep, tail = rule.partition(" if ")
    if not sep or not head.strip() or not tail.strip():
        raise _rule_error(rule, "expected '<value> if <condition> [and <condition> ...]'")
    conditions = []
    for part in re.split(r"\s+and\s+", tail.strip()):
        conditions.append(_condition(rule, part.strip()))
    return _literal_or_ref(head.strip()), conditions


def _literal_or_ref(text: str) -> str:
    return text if text.startswith("@") else str(_literal(text))


def _condition(rule: str, text: str) -> tuple[str, str, Any]:
    m = re.fullmatch(r"([A-Za-z0-9_.-]+)\s+is\s+(not\s+)?null", text)
    if m:
        return m.group(1), "is not null" if m.group(2) else "is null", None
    m = re.fullmatch(r"([A-Za-z0-9_.-]+)\s+(not\s+in|in)\s+(\[[^\]]*\])", text)
    if m:
        op = "not in" if m.group(2).startswith("not") else "in"
        return m.group(1), op, _list(m.group(3))
    m = re.fullmatch(r"([A-Za-z0-9_.-]+)\s*(==|!=|<=|>=|<|>)\s*(.+)", text)
    if m:
        return m.group(1), m.group(2), _literal(m.group(3).strip())
    raise _rule_error(rule, f"cannot read the condition {text!r}")


def _rule_error(rule: str, why: str) -> Exception:
    return refuse(
        "params_invalid",
        f'Rule {rule!r}: {why}. Write rules as "high if cv_tier == medium and pp_near == true".',
        {"param": "rules", "rule": rule},
    )


def _holds(value: Any, op: str, literal: Any) -> bool:
    if op == "is null":
        return value is None
    if op == "is not null":
        return value is not None
    if op == "in":
        return value in literal
    if op == "not in":
        return value not in literal
    if op == "==":
        return bool(value == literal)
    if op == "!=":
        return bool(value != literal)
    if value is None:
        return False
    try:
        if op == "<":
            return bool(value < literal)
        if op == "<=":
            return bool(value <= literal)
        if op == ">":
            return bool(value > literal)
        return bool(value >= literal)
    except TypeError:
        raise refuse(
            "rule_type_mismatch",
            f"Cannot compare {type(value).__name__} {value!r} with {literal!r} using {op}.",
        ) from None


RULE_MANIFEST = manifest(
    "rule_label",
    "labeler",
    "Writes one column from ordered rules over other columns; the first rule that holds wins, "
    'else the default. A rule reads "high if cv_tier == medium and pp_near == true"; a value '
    'may copy a column ("@cv_tier"). Ops: == != < <= > >= in [a, b] not in [a, b] is null '
    "is not null. Literals: true, false, null, numbers, 'quoted' text or bare words; a value is "
    "compared with its own type, never converted. Deterministic, no model.",
    {
        "output_column": {
            "type": "string",
            "minLength": 1,
            "pattern": "^[A-Za-z0-9_]{1,64}$",
            "default": "label",
            "title": "Output column",
        },
        "rules": {
            "type": "array",
            "items": {"type": "string", "minLength": 6, "maxLength": 2000},
            "minItems": 1,
            "maxItems": 100,
            "title": "Rules",
        },
        "default": {
            "type": "string",
            "title": "Default",
            "x-hint": "When no rule holds; @column copies a column. Unset: null.",
        },
    },
    ["rules"],
    output_columns=(ColumnSpec(name="label", type="string", role="metadata"),),
)


class RuleLabel:
    manifest = RULE_MANIFEST

    @staticmethod
    def _parsed(params: Mapping[str, Any]) -> list[tuple[str, list[tuple[str, str, Any]]]]:
        return [parse_rule(str(r)) for r in params["rules"]]

    def columns_for(self, params: Mapping[str, Any]) -> tuple[list[str], list[str], list[str]]:
        reads: list[str] = []
        try:
            parsed = self._parsed(params)
        except Exception:  # noqa: BLE001 - a bad rule is reported by run/validation
            parsed = []
        for value, conditions in parsed:
            if value.startswith("@"):
                reads.append(value[1:])
            reads.extend(column for column, _, _ in conditions)
        default = params.get("default")
        if isinstance(default, str) and default.startswith("@"):
            reads.append(default[1:])
        return sorted(set(reads)), [str(params.get("output_column") or "label")], []

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        parsed = self._parsed(params)
        reads, writes, _ = self.columns_for(params)
        data = {c: column_values(batch, c, "rule column") for c in reads}
        default = params.get("default")
        out: list[str | None] = []
        for i in range(batch.num_rows):
            chosen: Any = None
            matched = False
            for value, conditions in parsed:
                if all(_holds(data[c][i], op, lit) for c, op, lit in conditions):
                    chosen, matched = value, True
                    break
            if not matched:
                chosen = default
            if isinstance(chosen, str) and chosen.startswith("@"):
                chosen = data[chosen[1:]][i]
            out.append(None if chosen is None else _as_text(chosen))
        name = writes[0]
        output = append_outputs(batch, [(name, pa.array(out, pa.string()))], self.manifest.ref_text)
        return OperatorResult(output=output, output_roles={name: "metadata"})


def _as_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


OPERATORS: tuple[type, ...] = (RegexMatch, PhraseProximity, RuleLabel)
