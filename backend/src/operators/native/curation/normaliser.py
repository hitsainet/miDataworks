"""``normaliser``: chat content to one ``messages`` form, and metadata renames (FR-004.9).

Accepted chat forms (FTDD 004 §6.2 leaves the list to the implementation): a list of
``{role, content}`` and the ``{from, value}`` conversation form (ShareGPT). Roles map to
``system``/``user``/``assistant`` (``human`` -> user; ``gpt``, ``bot``, ``model`` -> assistant).
Content is copied VERBATIM: never a newline, role marker or line of code stripped, and no text
cleaning as a side effect (miStudio's forced cleaning stripped every newline from a corpus).

A row rewritten gets one ``changed`` event (``normalised``, the fields named); a row that cannot be
mapped is dropped with ``unmappable``, naming the missing or malformed field. Renames apply to
metadata columns only: renaming a content column would change what feature 002's row key is
computed over, so it is refused with what to do instead.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pyarrow as pa

from ....models.enums import ColumnRole
from ...context import RunContext
from ...protocol import OperatorResult
from .common import manifest, param, refuse

ROLE_MAP = {
    "system": "system",
    "user": "user",
    "human": "user",
    "assistant": "assistant",
    "gpt": "assistant",
    "bot": "assistant",
    "model": "assistant",
}


class Unmappable(ValueError):
    def __init__(self, field: str, why: str) -> None:
        super().__init__(why)
        self.field = field
        self.why = why


def normalise_chat(
    value: Any, column: str, role_map: Mapping[str, str] = ROLE_MAP
) -> list[dict[str, str]]:
    """One chat cell to ``[{role, content}]``; raises :class:`Unmappable` naming the field.

    ``role_map`` is the normaliser's own :data:`ROLE_MAP` unless a caller widens it
    (``chat_json_parser`` adds ``tool``)."""
    if not isinstance(value, list) or not value:
        raise Unmappable(column, f"{column} is not a non-empty list of messages")
    out = []
    for index, message in enumerate(value):
        if not isinstance(message, dict):
            raise Unmappable(f"{column}[{index}]", "a message is not an object")
        role = message.get("role", message.get("from"))
        content = message.get("content", message.get("value"))
        if not isinstance(role, str) or role.lower() not in role_map:
            raise Unmappable(f"{column}[{index}].role", f"role {role!r} is not recognised")
        if not isinstance(content, str):
            raise Unmappable(f"{column}[{index}].content", "content is missing or not text")
        out.append({"role": role_map[role.lower()], "content": content})
    return out


def _renames(params: Mapping[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in param(params, "rename", []):
        source, sep, target = str(item).partition("=")
        if not sep or not source or not target:
            raise refuse("params_invalid", f"Rename {item!r} must look like source=target.")
        out[source] = target
    return out


class Normaliser:
    manifest = manifest(
        "normaliser",
        "mapper",
        "Converts chat content to one messages form (role and content) and renames metadata "
        "columns; never strips newlines, role markers or code.",
        params={
            "chat_columns": {
                "type": "array",
                "items": {"type": "string"},
                "default": ["messages"],
                "title": "Chat columns",
            },
            "rename": {
                "type": "array",
                "items": {"type": "string", "pattern": "^[^=]+=[^=]+$"},
                "default": [],
                "title": "Rename metadata columns (source=target)",
                "x-advanced": True,
            },
        },
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        renames = _renames(params)
        for source in renames:
            if ctx.column_roles.get(source) == ColumnRole.CONTENT:
                raise refuse(
                    "params_invalid",
                    f"{source!r} is a content column; renaming it would change every row key. "
                    "Choose the column at import, or keep its name.",
                    {"column": source},
                )
        if batch.num_rows == 0:
            return OperatorResult(output=batch)
        chat = [c for c in param(params, "chat_columns", ["messages"]) if c in batch.schema.names]
        rows = batch.to_pylist()
        out_rows, events = [], []
        for row in rows:
            pair = (row["_dw_row_key"], row["_dw_occurrence"])
            changed: list[str] = []
            try:
                for column in chat:
                    new = normalise_chat(row[column], column)
                    if new != row[column]:
                        row[column] = new
                        changed.append(column)
            except Unmappable as exc:
                events.append(
                    ctx.drop(pair, "unmappable", f"{exc.field}: {exc.why}", "field", text=exc.field)
                )
                continue
            if changed:
                event = ctx.change(
                    pair,
                    ctx.row_key(row),
                    "normalised",
                    f"normalised {', '.join(changed)}",
                    "fields",
                    text=",".join(changed),
                )
                row["_dw_row_key"], row["_dw_occurrence"] = event.new_row_key, event.new_occurrence
                events.append(event)
            out_rows.append(row)
        schema = batch.schema
        if chat:
            message = pa.list_(pa.struct([("role", pa.string()), ("content", pa.string())]))
            for column in chat:
                schema = schema.set(schema.get_field_index(column), pa.field(column, message))
        output = pa.Table.from_pylist(out_rows, schema=schema)
        if renames:
            output = output.rename_columns([renames.get(n, n) for n in output.schema.names])
        roles = {renames[s]: ctx.column_roles.get(s, ColumnRole.METADATA) for s in renames}
        return OperatorResult(output=output, events=events, output_roles=roles or None)
