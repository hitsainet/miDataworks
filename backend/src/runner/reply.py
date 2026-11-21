"""Reduce the guest's reply to a count or a reason (FTDD §4.4 "The host decides validity").

The grammar is the output barrier: whatever the guest does, the host returns a non-negative ``int``
clamped at ``count_cap``, or a contract reason. ``bool`` is not an ``int`` here (M1); the clamp is
load-bearing (M2); nothing of the guest's own bytes is ever forwarded (M7); ``diag`` survives only in
``dry_run`` (M8, stripped by the caller of :func:`reduce`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from src.runner_contract.reasons import Reason

_DECIMAL = re.compile(r"(0|[1-9][0-9]{0,18})")
_TYPED = frozenset({"bool", "float", "str", "bytes", "none", "other"})
_FAIL_REASON: dict[str, Reason] = {
    "load_refused": Reason.RULE_LOAD_REFUSED,
    "error": Reason.RULE_ERROR,
    "recursion": Reason.RULE_RESOURCE_LIMIT,
    "memory": Reason.RULE_RESOURCE_LIMIT,
}
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
_MODULE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,127}")


@dataclass
class Reduced:
    """One call's outcome: ``count`` or ``reason``; ``diag``/``refused``/``inspect`` when present."""

    count: int | None = None
    reason: Reason | None = None
    diag: dict[str, Any] | None = None
    refused: str | None = None
    inspect: dict[str, list[str]] | None = field(default=None)


INVALID = Reason.RULE_OUTPUT_INVALID


def _invalid() -> Reduced:
    return Reduced(reason=INVALID)


def _diag(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or set(value) != {"exception_type", "line", "message"}:
        return None
    etype, line, message = value["exception_type"], value["line"], value["message"]
    if not isinstance(etype, str) or len(etype) > 100 or not isinstance(message, str):
        return None
    if len(message) > 200 or not (line is None or (type(line) is int and line >= 0)):
        return None
    return {"exception_type": etype, "line": line, "message": message}


def _one_line(raw: bytes, output_limit: int) -> Any:
    if not raw or len(raw) > output_limit or not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        return None
    try:
        return json.loads(raw[:-1].decode("ascii"))
    except (UnicodeDecodeError, ValueError):
        return None


def _fail(phase: str, result: dict[str, Any]) -> Reduced:
    kind = result.get("fail")
    reason = _FAIL_REASON.get(kind) if isinstance(kind, str) else None
    if reason is None:
        return _invalid()
    if kind == "load_refused":
        refused = result.get("refused")
        if set(result) != {"fail", "refused"} or not isinstance(refused, str):
            return _invalid()
        return Reduced(reason=reason, refused=refused[:128])
    if not set(result) <= {"fail", "diag"}:
        return _invalid()
    diag = None
    if "diag" in result:
        diag = _diag(result["diag"])
        if diag is None:
            return _invalid()
    return Reduced(reason=reason, diag=diag)


def reduce(raw: bytes, *, mode: str, output_limit: int, count_cap: int) -> Reduced:
    """The host's reading of one reply buffer; ``mode`` is the guest request mode."""
    reply = _one_line(raw, output_limit)
    if not isinstance(reply, dict):
        return _invalid()
    phase = reply.get("phase")
    if mode == "inspect":
        if phase != "load":
            return _invalid()
        if set(reply) == {"phase", "inspect"}:
            return _inspect(reply["inspect"])
    if set(reply) != {"phase", "result"} or phase not in ("load", "call"):
        return _invalid()
    result = reply["result"]
    if not isinstance(result, dict):
        return _invalid()
    if "fail" in result:
        return _fail(phase, result)
    if mode == "inspect" or phase != "call":
        return _invalid()
    kind = result.get("t")
    if kind == "int" and set(result) == {"t", "v"}:
        value = result["v"]
        if value == "overflow":
            return Reduced(count=count_cap)
        if isinstance(value, str) and _DECIMAL.fullmatch(value):
            return Reduced(count=min(int(value), count_cap))
        return _invalid()
    if kind in _TYPED and set(result) == {"t"}:
        return _invalid()
    return _invalid()


def _inspect(body: Any) -> Reduced:
    if not isinstance(body, dict) or set(body) != {"functions", "imports"}:
        return _invalid()
    functions, imports = body["functions"], body["imports"]
    if not (isinstance(functions, list) and isinstance(imports, list)):
        return _invalid()
    if not all(isinstance(f, str) and _NAME.fullmatch(f) for f in functions):
        return _invalid()
    if not all(isinstance(m, str) and _MODULE.fullmatch(m) for m in imports):
        return _invalid()
    return Reduced(inspect={"functions": sorted(set(functions)), "imports": sorted(set(imports))})
