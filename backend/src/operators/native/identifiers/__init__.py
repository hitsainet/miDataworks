"""Identifier and pattern operators (end-to-end run gaps G0, G3, G8; 2026-10-21): ``IDENTIFIER_OPERATORS`` is
THE list, imported once by ``operators/native/registrations.py``. The reachability test builds the
live registry and finds every entry; deleting that import turns it red."""

from __future__ import annotations

from .drop_columns import OPERATORS as _DROP
from .identifier_ops import OPERATORS as _IDENTIFIERS
from .text_ops import OPERATORS as _TEXT

IDENTIFIER_OPERATORS: tuple[type, ...] = (*_IDENTIFIERS, *_TEXT, *_DROP)
