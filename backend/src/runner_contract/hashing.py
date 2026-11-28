"""One hash rule for the API, the workers, the runner and the tests (FR-018.8; FTDD §4.2).

``code_sha256`` = SHA-256 of ``core.canonical_json`` of the code-rule document: keys sorted by code
point (not UTF-16), compact separators, UTF-8 with non-ASCII as itself, NaN and infinity refused. The
published document file is exactly :func:`canonical_document_bytes`. ``bundle_digest`` is the same
hash over ``guest.bundle`` without its own ``bundle_digest`` key. Standard library only (through
``core/canonical_json.py``): the runner image imports this module.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from src.core.canonical_json import canonical_json


def canonical_document_bytes(document: Mapping[str, Any]) -> bytes:
    """The exact bytes hashed and published for a code-rule document."""
    return canonical_json(dict(document))


def code_sha256(document: Mapping[str, Any]) -> str:
    """The code rule's identity: the approval, the rule set's ``code_sha256`` and the runner's key."""
    return hashlib.sha256(canonical_document_bytes(document)).hexdigest()


def bytes_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def bundle_digest(bundle: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical JSON of the bundle block without ``bundle_digest``."""
    return hashlib.sha256(
        canonical_json({k: v for k, v in bundle.items() if k != "bundle_digest"})
    ).hexdigest()
