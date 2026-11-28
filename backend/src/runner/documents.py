"""Code-rule documents as the runner receives them: checked, then cached by hash (FTID §3.2, I10).

The runner checks only what it needs to run safely and honestly: the bytes are the canonical JSON of
a document of this contract's major (FR-018.8), they hash to the ``code_sha256`` the caller names,
and the function asked for is declared. Approval is NOT checked here: it lives where the database is.
An LRU of the last N documents by hash keeps batch bodies small; a hash not in it is answered 409
``document_not_cached`` and the caller resends the bytes once.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from src.core.canonical_json import canonical_json
from src.runner_contract.hashing import bytes_sha256

DOCUMENT_KEYS = frozenset(
    {"runner_contract", "source", "functions", "params_schema", "derived_from"}
)


class DocumentRefused(Exception):
    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class Document:
    code_sha256: str
    source: str
    functions: frozenset[str]


def parse(
    code_sha256: str, canonical_utf8: str, contract_format: str, max_source_bytes: int
) -> Document:
    raw = canonical_utf8.encode("utf-8", errors="strict")
    if bytes_sha256(raw) != code_sha256:
        raise DocumentRefused("document_hash_mismatch", "the bytes do not hash to code_sha256")
    try:
        doc: Any = json.loads(raw)
    except ValueError as exc:
        raise DocumentRefused("document_invalid", "not JSON") from exc
    if not isinstance(doc, dict) or set(doc) != DOCUMENT_KEYS:
        raise DocumentRefused("document_invalid", "not a code-rule document")
    if canonical_json(doc) != raw:
        raise DocumentRefused("document_invalid", "not the canonical bytes")
    contract = doc["runner_contract"]
    if contract != contract_format:
        raise DocumentRefused("runner_contract_unknown", "this runner serves " + contract_format)
    source, functions = doc["source"], doc["functions"]
    if not isinstance(source, str) or len(source.encode("utf-8")) > max_source_bytes:
        raise DocumentRefused("document_invalid", "source missing or over the runner's ceiling")
    if not isinstance(functions, list) or not all(
        isinstance(f, dict) and isinstance(f.get("name"), str) for f in functions
    ):
        raise DocumentRefused("document_invalid", "functions malformed")
    return Document(code_sha256, source, frozenset(f["name"] for f in functions))


class DocumentCache:
    def __init__(self, size: int) -> None:
        self._size = size
        self._items: OrderedDict[str, Document] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, code_sha256: str) -> Document | None:
        with self._lock:
            doc = self._items.get(code_sha256)
            if doc is not None:
                self._items.move_to_end(code_sha256)
            return doc

    def put(self, doc: Document) -> None:
        with self._lock:
            self._items[doc.code_sha256] = doc
            self._items.move_to_end(doc.code_sha256)
            while len(self._items) > self._size:
                self._items.popitem(last=False)
