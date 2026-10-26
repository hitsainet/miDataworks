"""A structured difference of two rule-set documents (FR-016.21; FTDD 016 section 7.3, Q24 (a)).

Each entry is ``{"op": "add" | "remove" | "replace", "path": <RFC 6901 JSON Pointer>, "old"?, "new"?}``.
Objects are compared key by key. A list whose every item is an object with an ``id`` (signals,
vetoes, decoys, substitutions, cases) is matched BY ``id``, so moving an item is no difference and a
changed item is reported inside it; the pointer uses the item's index in ``a`` (or in ``b`` for an
added item). Any other list is compared by index. Values compare with their JSON type: ``1`` and
``true`` differ. Pure: no I/O.
"""

from __future__ import annotations

from typing import Any


def _esc(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


def _same(a: Any, b: Any) -> bool:
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    return bool(a == b)


def _by_id(items: list[Any]) -> dict[Any, int] | None:
    if not items or not all(isinstance(i, dict) and "id" in i for i in items):
        return None
    index = {item["id"]: n for n, item in enumerate(items)}
    return index if len(index) == len(items) else None


def _walk(a: Any, b: Any, path: str, out: list[dict[str, Any]]) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            sub = f"{path}/{_esc(str(key))}"
            if key not in b:
                out.append({"op": "remove", "path": sub, "old": a[key]})
            elif key not in a:
                out.append({"op": "add", "path": sub, "new": b[key]})
            else:
                _walk(a[key], b[key], sub, out)
        return
    if isinstance(a, list) and isinstance(b, list):
        ia, ib = _by_id(a), _by_id(b)
        if ia is not None and (ib is not None or not b):
            ib = ib or {}
            for ident, n in ia.items():
                if ident in ib:
                    _walk(a[n], b[ib[ident]], f"{path}/{n}", out)
                else:
                    out.append({"op": "remove", "path": f"{path}/{n}", "old": a[n]})
            for ident, n in ib.items():
                if ident not in ia:
                    out.append({"op": "add", "path": f"{path}/{n}", "new": b[n]})
            return
        if ib is not None and not a:
            for n, item in enumerate(b):
                out.append({"op": "add", "path": f"{path}/{n}", "new": item})
            return
        for n in range(max(len(a), len(b))):
            sub = f"{path}/{n}"
            if n >= len(b):
                out.append({"op": "remove", "path": sub, "old": a[n]})
            elif n >= len(a):
                out.append({"op": "add", "path": sub, "new": b[n]})
            else:
                _walk(a[n], b[n], sub, out)
        return
    if not _same(a, b):
        out.append({"op": "replace", "path": path, "old": a, "new": b})


def difference(a: Any, b: Any) -> list[dict[str, Any]]:
    """Every difference from ``a`` to ``b``, in document order of the keys (sorted)."""
    out: list[dict[str, Any]] = []
    _walk(a, b, "", out)
    return out


def unescape(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def apply_pointer(document: Any, pointer: str, value: Any) -> Any:
    """A deep copy of ``document`` with the value at ``pointer`` replaced (RFC 6901). The pointer
    must name an existing member or index; ``KeyError`` / ``IndexError`` otherwise."""
    import copy

    out = copy.deepcopy(document)
    if pointer == "":
        return copy.deepcopy(value)
    if not pointer.startswith("/"):
        raise KeyError(pointer)
    tokens = [unescape(t) for t in pointer[1:].split("/")]
    node = out
    for token in tokens[:-1]:
        node = node[int(token)] if isinstance(node, list) else node[token]
    last = tokens[-1]
    if isinstance(node, list):
        index = int(last)
        if not 0 <= index < len(node):
            raise IndexError(pointer)
        node[index] = copy.deepcopy(value)
    else:
        if last not in node:
            raise KeyError(pointer)
        node[last] = copy.deepcopy(value)
    return out
