"""``chat_json_parser``: a chat stored as JSON TEXT, parsed into a ``messages`` column.

Found by the 2026-10-07 live check of the probe reproduction gate: ``Arrrlex/models-under-pressure``
stores every chat as a JSON string (``'[{"role": "system", ...}, {"role": "user", ...}]'``), and the
``normaliser`` accepts only a real list, so it would drop every such row as ``unmappable``. Nothing
could turn the text into messages, so a probe run sent the JSON characters to miLLM as ONE user
turn — a probe reading brackets and quotes, while miStudio had decoded the same rows as chats.

This operator parses each named column IN PLACE (the column keeps its name and its ``content``
role, so a later step, a link or a run names the same column) into ``list<struct<role, content>>``.

Accepted, as measured on every config of ``Arrrlex/models-under-pressure`` (2026-10-08, the
datasets-server rows of each split): a JSON array of ``{role, content}`` objects with roles
``system``, ``user``, ``assistant`` and ``tool`` (``toolace_*`` carries ``tool`` turns), and the
ShareGPT ``{from, value}`` form the normaliser already reads. Roles map through the normaliser's own
table plus ``tool``; content is copied verbatim (never stripped, never cleaned).

Never guessed — each of these drops the row with a reason naming the column and what was found:

- ``chat_json_unparseable``: the value is missing, empty, or not valid JSON (``"nan"`` included);
- ``chat_json_not_a_chat``: valid JSON that is not a non-empty list of messages, a message whose
  role is not recognised, whose content is missing or not text, or which carries fields this column
  type cannot hold (``tool_calls``, ``name`` ...: dropping them silently would change the chat);
- a JSON STRING (``'"I am anxious ..."'``, 7.5% of the ``training`` config) is a value, not a chat:
  dropped unless ``json_string_rows`` is ``user_turn``, which reads the decoded string as ONE user
  turn because the operator said so.

A parsed row gets one ``changed`` event (``parsed_json_chat``): its content changed from text to
messages, so feature 002's row key is recomputed (identical chats still share one key).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pyarrow as pa

from ...context import RunContext
from ...protocol import OperatorResult
from .common import manifest, param, refuse
from .normaliser import ROLE_MAP, Unmappable, normalise_chat

#: The normaliser's roles plus OpenAI's ``tool`` (``toolace_*`` in models-under-pressure).
CHAT_ROLES: dict[str, str] = {**ROLE_MAP, "tool": "tool"}
#: The fields one message may carry: the two forms ``normalise_chat`` reads.
MESSAGE_FIELDS = frozenset({"role", "content", "from", "value"})
MESSAGES_TYPE = pa.list_(pa.struct([("role", pa.string()), ("content", pa.string())]))

UNPARSEABLE = "chat_json_unparseable"
NOT_A_CHAT = "chat_json_not_a_chat"
PARSED = "parsed_json_chat"


class Dropped(ValueError):
    """A value this parser will not read as a chat, with the reason code and the field."""

    def __init__(self, code: str, field: str, why: str) -> None:
        super().__init__(why)
        self.code = code
        self.field = field
        self.why = why


def parse_chat_json(value: Any, column: str, json_string_rows: str) -> list[dict[str, str]]:
    """One JSON-text cell to ``[{role, content}]``, or :class:`Dropped` saying why not."""
    if value is None:
        raise Dropped(UNPARSEABLE, column, f"{column} is missing")
    if not isinstance(value, str):
        raise Dropped(UNPARSEABLE, column, f"{column} holds {type(value).__name__}, not text")
    if not value.strip():
        raise Dropped(UNPARSEABLE, column, f"{column} is empty")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        shown = value if len(value) <= 40 else value[:40] + "..."
        raise Dropped(
            UNPARSEABLE, column, f"{column} is not valid JSON ({exc.msg}; value {shown!r})"
        ) from None
    if isinstance(decoded, str):
        if json_string_rows == "user_turn":
            return [{"role": "user", "content": decoded}]
        raise Dropped(
            NOT_A_CHAT,
            column,
            f"{column} holds a JSON string, not a list of messages; set json_string_rows to "
            "user_turn to read such a value as one user turn",
        )
    if not isinstance(decoded, list):
        raise Dropped(
            NOT_A_CHAT, column, f"{column} holds a JSON {type(decoded).__name__}, not a list"
        )
    for index, message in enumerate(decoded):
        if isinstance(message, dict):
            extra = sorted(set(message) - MESSAGE_FIELDS)
            if extra:
                raise Dropped(
                    NOT_A_CHAT,
                    f"{column}[{index}]",
                    f"a message carries {extra}, which a role-and-content chat cannot hold",
                )
    try:
        return normalise_chat(decoded, column, CHAT_ROLES)
    except Unmappable as exc:
        raise Dropped(NOT_A_CHAT, exc.field, exc.why) from None


class ChatJsonParser:
    manifest = manifest(
        "chat_json_parser",
        "mapper",
        "Parses a chat stored as JSON text into a messages column (role and content), in place; "
        "a value that is not a chat is dropped with the reason, never guessed.",
        params={
            "columns": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "minItems": 1,
                "title": "Columns holding a chat as JSON text",
            },
            "json_string_rows": {
                "type": "string",
                "enum": ["drop", "user_turn"],
                "default": "drop",
                "title": "A value that is a JSON string, not a chat",
                "x-advanced": True,
            },
        },
        required=("columns",),
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        columns = list(param(params, "columns", []))
        strings = str(param(params, "json_string_rows", "drop"))
        for column in columns:
            if column not in batch.schema.names:
                raise refuse(
                    "params_invalid",
                    f"The input has no column {column!r}. Choose one of: "
                    + ", ".join(n for n in batch.schema.names if not n.startswith("_dw_")),
                    {"column": column},
                )
            kind = batch.schema.field(column).type
            if not (pa.types.is_string(kind) or pa.types.is_large_string(kind)):
                raise refuse(
                    "params_invalid",
                    f"{column!r} holds {kind}, not JSON text. A column that already holds a list "
                    "of messages is read by the normaliser.",
                    {"column": column, "type": str(kind)},
                )
        schema = batch.schema
        for column in columns:
            schema = schema.set(schema.get_field_index(column), pa.field(column, MESSAGES_TYPE))
        if batch.num_rows == 0:
            return OperatorResult(output=pa.Table.from_pylist([], schema=schema))
        out_rows, events = [], []
        for row in batch.to_pylist():
            pair = (row["_dw_row_key"], row["_dw_occurrence"])
            try:
                for column in columns:
                    row[column] = parse_chat_json(row[column], column, strings)
            except Dropped as exc:
                events.append(
                    ctx.drop(pair, exc.code, f"{exc.field}: {exc.why}", "field", text=exc.field)
                )
                continue
            event = ctx.change(
                pair,
                ctx.row_key(row),
                PARSED,
                f"parsed {', '.join(columns)} from JSON text into messages",
                "fields",
                text=",".join(columns),
            )
            row["_dw_row_key"], row["_dw_occurrence"] = event.new_row_key, event.new_occurrence
            events.append(event)
            out_rows.append(row)
        return OperatorResult(output=pa.Table.from_pylist(out_rows, schema=schema), events=events)
