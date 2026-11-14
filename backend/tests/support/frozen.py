"""The ``@exercises`` registry of the frozen-identifier tests (feature 020; FR-020.1; FTID §8.2).

A frozen-identifier test names the register entry it proves::

    @exercises("DR-08", literals=("X-miLLM-Strict", "X-miLLM-Seed"))
    def test_dr08_headers_sent_by_the_real_paths(literal: str) -> None: ...

``literals`` also PARAMETRISES the test over ``literal``, so declaring a literal forces the body to
exercise it (a declared literal the body ignores still runs as its own case and must pass on its
own). ``tests/unit/test_legacy_identifiers_file.py`` compares this registry with the committed
export both ways.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

import pytest

F = TypeVar("F", bound=Callable[..., Any])

#: entry id -> [(module, function, literals)]
REGISTRY: dict[str, list[tuple[str, str, tuple[str, ...]]]] = {}


def exercises(dr_id: str, *, literals: tuple[str, ...] = ()) -> Callable[[F], F]:
    def register(function: F) -> F:
        rows = REGISTRY.setdefault(dr_id, [])
        row = (function.__module__, function.__name__, tuple(literals))
        if row not in rows:
            rows.append(row)
        if literals:
            return pytest.mark.parametrize("literal", literals)(function)  # type: ignore[no-any-return]
        return function

    return register
