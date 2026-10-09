"""Row keys, scheme ``dw.rowkey/v1`` (FR-002.19, FR-002.21, FR-002.22; FTDD 002 section 4.4).

A row key is the SHA-256 of the scheme identifier, a newline, and the canonical JSON of the row's
content-column values after key normalisation. One implementation: assembly, ingestion's key
verification, and feature 003's executor all call :func:`compute_row_key`
(``tests/unit/test_single_function_call_sites.py`` holds every ``hashlib.sha256`` call in
``services/`` and ``workers/`` to this module and ``identity.py``).

v1 normalisation, applied to every string inside the content values, including strings nested in
chat messages: Unicode NFC; ``\\r\\n`` and ``\\r`` become ``\\n``; leading and trailing whitespace
removed. Nothing else changes: case, inner spacing and punctuation are content (T-08). Dictionary
keys are not normalised. Normalisation computes the key only; stored rows are never rewritten.

What it refuses: bytes, non-finite floats, decimals, timestamps and any other type
(``rowkey_unsupported_type``); a content column the row does not have (``rowkey_missing_column``);
an unknown scheme (``rowkey_scheme_unknown``). A rule change is a NEW scheme entry, never an edit of
``dw.rowkey/v1``: the scheme prefix in the hashed bytes makes keys of two schemes disjoint.
"""

from __future__ import annotations

import hashlib
import math
import unicodedata
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import Any

from ..core.canonical_json import canonical_json

ROWKEY_V1 = "dw.rowkey/v1"

#: Rows hashed per ``to_pylist`` chunk in :func:`compute_row_keys`.
BATCH_CHUNK_ROWS = 10_000


class RowKeyError(ValueError):
    """A row cannot be keyed. ``code`` is the refusal code the API and builds report."""

    code = "rowkey_error"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class RowKeyUnsupportedType(RowKeyError):
    code = "rowkey_unsupported_type"

    def __init__(self, column: str, type_name: str) -> None:
        super().__init__(
            f"Column {column!r} holds a {type_name}, which dw.rowkey/v1 cannot key. Mark the "
            "column as metadata, or convert it with an operator before keying.",
            column=column,
            type=type_name,
        )


class RowKeyMissingColumn(RowKeyError):
    code = "rowkey_missing_column"

    def __init__(self, column: str) -> None:
        super().__init__(
            f"The row has no content column {column!r}. Check the column roles.", column=column
        )


class RowKeySchemeUnknown(RowKeyError):
    code = "rowkey_scheme_unknown"

    def __init__(self, scheme: str) -> None:
        super().__init__(
            f"Row-key scheme {scheme!r} is not registered; known: {sorted(ROWKEY_SCHEMES)}.",
            scheme=scheme,
        )


class _Unsupported(Exception):
    def __init__(self, type_name: str) -> None:
        self.type_name = type_name


def _normalise_v1(value: Any) -> Any:
    """Key normalisation for ``dw.rowkey/v1`` (FTDD 002 section 4.4), recursively."""
    if isinstance(value, str):
        text = unicodedata.normalize("NFC", value)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        return text.strip()
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _Unsupported("non-finite float")
        return value
    if isinstance(value, list | tuple):
        return [_normalise_v1(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalise_v1(item) for key, item in value.items()}
    raise _Unsupported(type(value).__name__)


#: The scheme registry. ``dw.rowkey/v2`` would be a new entry, never an edit of v1.
ROWKEY_SCHEMES: dict[str, Callable[[Any], Any]] = {ROWKEY_V1: _normalise_v1}


def normaliser(scheme: str) -> Callable[[Any], Any]:
    try:
        return ROWKEY_SCHEMES[scheme]
    except KeyError:
        raise RowKeySchemeUnknown(scheme) from None


def compute_row_key(
    row: Mapping[str, Any], content_columns: Sequence[str], scheme: str = ROWKEY_V1
) -> str:
    """The row key (64 lowercase hex characters) of ``row`` under ``scheme``."""
    normalise = normaliser(scheme)
    content: dict[str, Any] = {}
    for column in sorted(content_columns):
        if column not in row:
            raise RowKeyMissingColumn(column)
        try:
            content[column] = normalise(row[column])
        except _Unsupported as exc:
            raise RowKeyUnsupportedType(column, exc.type_name) from None
    payload = scheme.encode("utf-8") + b"\n" + canonical_json(content)
    return hashlib.sha256(payload).hexdigest()


def compute_row_keys(
    batch: Any, content_columns: Sequence[str], scheme: str = ROWKEY_V1
) -> list[str]:
    """Keys for every row of a pyarrow ``RecordBatch`` or ``Table``, in row order.

    Iterates in chunks of :data:`BATCH_CHUNK_ROWS`, converting only the content columns, so a
    batch is never materialised whole as Python objects. The per-row function stays the only
    hashing code.
    """
    missing = [c for c in content_columns if c not in batch.schema.names]
    if missing:
        raise RowKeyMissingColumn(missing[0])
    projected = batch.select(list(content_columns))
    keys: list[str] = []
    for rows in _chunks(projected):
        keys.extend(compute_row_key(row, content_columns, scheme) for row in rows)
    return keys


def _chunks(batch: Any) -> Iterator[list[dict[str, Any]]]:
    for start in range(0, batch.num_rows, BATCH_CHUNK_ROWS):
        yield batch.slice(start, BATCH_CHUNK_ROWS).to_pylist()
