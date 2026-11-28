"""A runner with NO barrier, for one negative control only (feature 018; FTDD §11.4; G3 (a); M9).

``InProcessExecRunner`` runs a code rule's source with the host's own ``exec`` in the calling
process: one module namespace per document, kept between calls, no fuel, no memory cap, the raw
return value passed through. It is everything the isolated runner exists to prevent, and it is used
ONLY by ``tests/unit/test_harness_sees_breaches.py``, which proves that the vector harness reports
those breaches as failures. Production code gets no bypass flag and nothing under ``src`` may import
this (``tests/unit/test_host_never_parses_code_rules.py`` refuses ``exec`` on code-rule source).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.runner_contract.hashing import code_sha256


class InProcessExecRunner:
    """Runs ``function(text, params)`` from a document's source in THIS process (no isolation)."""

    def __init__(self) -> None:
        self.namespaces: dict[str, dict[str, Any]] = {}

    def run(self, document: Mapping[str, Any], function: str, params: Any, text: str) -> Any:
        sha = code_sha256(dict(document))
        namespace = self.namespaces.get(sha)
        if namespace is None:
            namespace = {}
            code = compile(document["source"], f"<code rule {sha[:12]}>", "exec")
            exec(code, namespace)  # noqa: S102 - the point of this negative control
            self.namespaces[sha] = namespace
        try:
            return {"count": namespace[function](text, params)}
        except Exception:  # noqa: BLE001 - a raising rule is a rule_error, as in the runner
            return {"reason": "rule_error"}
