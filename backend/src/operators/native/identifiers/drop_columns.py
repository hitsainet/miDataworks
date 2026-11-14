"""``drop_columns@1`` (e2e run gap G8, 2026-10-21).

A 60-75-step derivation left every helper column in the published versions, where they collided
with the label-source declaration cap and fed D-3's audit columns that predict the label by
construction. This mapper removes named columns (or glob patterns) and nothing else: every row
is kept unchanged, so it emits no events.

It refuses, loudly: a named column that is not there, a pattern that matches nothing, a system
``_dw_`` column, and a CONTENT column (the row key is computed over content columns, so removing
one would change every row's identity; change the column's role instead).
"""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping
from typing import Any

import pyarrow as pa

from ...context import RunContext
from ...protocol import OperatorResult
from .common import SYSTEM_PREFIX, manifest, refuse

MANIFEST = manifest(
    "drop_columns",
    "mapper",
    "Removes columns from every row: named columns and/or glob patterns (vtmp_*, h?). Rows are "
    "kept unchanged. Refuses a name that is not there, a pattern that matches nothing, a _dw_ "
    "system column and a content column (the row key is computed over content columns).",
    {
        "columns": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 200},
            "uniqueItems": True,
            "maxItems": 2000,
            "title": "Columns",
        },
        "patterns": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 200},
            "uniqueItems": True,
            "maxItems": 200,
            "title": "Patterns",
            "x-hint": "Shell-style globs: * any run, ? one character, [abc] a set.",
        },
    },
    [],
)


def resolve(names: list[str], params: Mapping[str, Any], roles: Mapping[str, str]) -> list[str]:
    """The columns to drop from a table with columns ``names``, or a refusal naming the problem."""
    named = [str(c) for c in params.get("columns") or []]
    patterns = [str(p) for p in params.get("patterns") or []]
    if not named and not patterns:
        raise refuse("params_invalid", "Name at least one column or pattern to drop.")
    missing = [c for c in named if c not in names]
    if missing:
        raise refuse(
            "column_not_found",
            f"drop_columns names {missing}, which the input does not have.",
            {"missing": missing},
        )
    chosen = list(dict.fromkeys(named))
    for pattern in patterns:
        hits = [
            c for c in names if fnmatch.fnmatchcase(c, pattern) and not c.startswith(SYSTEM_PREFIX)
        ]
        if not hits:
            raise refuse(
                "column_not_found",
                f"The pattern {pattern!r} matches no column of the input.",
                {"pattern": pattern},
            )
        chosen.extend(c for c in hits if c not in chosen)
    system = [c for c in chosen if c.startswith(SYSTEM_PREFIX)]
    if system:
        raise refuse("column_protected", f"{system} are system columns and cannot be dropped.")
    content = [c for c in chosen if roles.get(c) == "content"]
    if content:
        raise refuse(
            "column_protected",
            f"{content} are content columns: the row key is computed over them, so dropping one "
            "would change every row's identity. Mark it metadata in the build's column_roles first.",
            {"columns": content},
        )
    return chosen


class DropColumns:
    manifest = MANIFEST

    def columns_for(self, params: Mapping[str, Any]) -> tuple[list[str], list[str], list[str]]:
        return (
            [str(c) for c in params.get("columns") or []],
            [],
            [str(c) for c in params.get("columns") or []],
        )

    def removes(self, params: Mapping[str, Any], columns: list[str]) -> list[str]:
        """What a step over ``columns`` would remove (patterns expanded), for recipe validation."""
        named = [c for c in params.get("columns") or [] if c in columns]
        out = list(named)
        for pattern in params.get("patterns") or []:
            out.extend(c for c in columns if fnmatch.fnmatchcase(c, str(pattern)) and c not in out)
        return out

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        drop = resolve(list(batch.schema.names), params, ctx.column_roles)
        return OperatorResult(output=batch.drop_columns(drop))


OPERATORS: tuple[type, ...] = (DropColumns,)
