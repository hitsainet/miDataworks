"""``regex_match@2``: ``regex_match@1``'s outputs on the rule-signal dialect (FR-016.12; OQ-3 (a)).

Same parameters and outputs as ``regex_match@1`` (``text_ops.RegexMatch``), plus the required
``fold_fullwidth_ascii`` (no default: ``@2`` has no existing recipes, and an option that changes what
matches is stated, never assumed). The pattern runs on RE2 through ``src/rules/dialect.py``: ASCII
classes, ``$`` only at the very end, linear time, spans in code points. A refused pattern names its
rule and code-point position. ``regex_match@1`` is unchanged and stays runnable (ADR-009); it is
listed NOT conformant (``rules/conformance.py``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pyarrow as pa

from ....core.canonical_json import canonical_json
from ....rules import dialect
from ...context import RunContext
from ...manifest import ColumnSpec
from ...protocol import OperatorResult
from ..identifiers.common import (
    PREFIX_PATTERN,
    append_outputs,
    manifest,
    output_names,
    refuse,
    text_values,
)

REGEX_OUTPUTS = ("matched", "count", "first", "start", "end", "spans")

REGEX_V2_MANIFEST = manifest(
    "regex_match",
    "labeler",
    "Matches a pattern of the rule-signal dialect (RE2: linear time; \\d \\w \\s \\b are ASCII; "
    "$ only at the end of the text) in a text column and records whether it matched, how often, "
    "the first match and its span, and every span (JSON [[start, end], ...], in characters). "
    "fold_fullwidth_ascii reads full-width ASCII forms as ASCII before matching. A refused pattern "
    "names its rule and position. Deterministic, no model.",
    {
        "column": {"type": "string", "minLength": 1, "title": "Column"},
        "pattern": {
            "type": "string",
            "minLength": 1,
            "maxLength": dialect.MAX_PATTERN_CHARS,
            "title": "Pattern",
        },
        "ignore_case": {"type": "boolean", "default": False, "title": "Ignore case"},
        "fold_fullwidth_ascii": {
            "type": "boolean",
            "title": "Fold full-width ASCII",
            "x-hint": "Read U+FF01-U+FF5E as ASCII and U+3000 as a space before matching; spans "
            "and substrings stay those of the original text.",
        },
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
    ["column", "pattern", "fold_fullwidth_ascii"],
    output_columns=(
        ColumnSpec(name="rx_matched", type="bool", role="metadata"),
        ColumnSpec(name="rx_count", type="int64", role="metadata"),
        ColumnSpec(name="rx_first", type="string", role="metadata", required=False),
        ColumnSpec(name="rx_start", type="int64", role="metadata", required=False),
        ColumnSpec(name="rx_end", type="int64", role="metadata", required=False),
        ColumnSpec(name="rx_spans", type="string", role="metadata"),
    ),
    version="2",
)


def compile_param(pattern: str, ignore_case: bool) -> Any:
    try:
        return dialect.compile_pattern(pattern, ignore_case)
    except dialect.PatternRefused as exc:
        raise refuse(
            "params_invalid",
            f"The pattern is outside the rule-signal dialect - {exc}.",
            {"param": "pattern", "rule": exc.rule, "position": exc.position},
        ) from None


class RegexMatchV2:
    manifest = REGEX_V2_MANIFEST

    def columns_for(self, params: Mapping[str, Any]) -> tuple[list[str], list[str], list[str]]:
        return [str(params["column"])], output_names(params, "rx_", REGEX_OUTPUTS), []

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        compiled = compile_param(str(params["pattern"]), bool(params.get("ignore_case", False)))
        fold = bool(params["fold_fullwidth_ascii"])
        keep = int(params.get("max_spans") or 100)
        cols: dict[str, list[Any]] = {k: [] for k in REGEX_OUTPUTS}
        for text in text_values(batch, str(params["column"])):
            if text is None:
                spans: list[tuple[int, int]] = []
            else:
                view = dialect.fold_fullwidth_ascii(text) if fold else text
                spans = [(m.start(), m.end()) for m in dialect.finditer(compiled, view)]
            cols["matched"].append(bool(spans))
            cols["count"].append(len(spans))
            # ``text is not None``, never truthiness: an empty match on "" has first "" (contract
            # case pattern.empty_text), as a rule set's ``first`` output does.
            cols["first"].append(
                text[spans[0][0] : spans[0][1]] if spans and text is not None else None
            )
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
