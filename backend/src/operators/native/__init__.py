"""The native provider (FR-003.13). ``native_operators()`` is THE list the registry reads.

One list, read by the registry and by the reachability test — never a second hand-kept copy
(FTDD 003 section 6.3). ``NATIVE_OPERATORS`` holds this feature's own entries (none in
production); product features extend ``registrations.PRODUCT_OPERATORS``.
"""

from __future__ import annotations

from .registrations import PRODUCT_OPERATORS, fixture_operators

#: Native operator classes owned by the framework itself (none: product operators come from 004,
#: 005, 007 and 009 through ``registrations.py``).
NATIVE_OPERATORS: tuple[type, ...] = ()


def native_operators() -> tuple[type, ...]:
    """Every native operator class this process offers, in registration order."""
    return NATIVE_OPERATORS + PRODUCT_OPERATORS + fixture_operators()
