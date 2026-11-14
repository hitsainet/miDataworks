"""Feature 001's vocabularies (001 FTASKS 2.1). Single source for CHECKs and ``/sources/meta``.

CHECK-constrained strings rather than native enum types, as Foundation's ``dw_jobs`` and feature
002 do (see ``models/enums.py`` for the reason).
"""

from __future__ import annotations

from enum import StrEnum


class SourceKind(StrEnum):
    HF = "hf"
    UPLOAD = "upload"


class SourceState(StrEnum):
    IMPORTING = "importing"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DELETED = "deleted"


class TokenTier(StrEnum):
    PER_IMPORT = "per_import"
    STORED = "stored"
    NONE = "none"


class AnnotationKind(StrEnum):
    TERMS = "terms"
    LICENCE = "licence"
    DETECTION_OVERRIDE = "detection_override"


class Redistribution(StrEnum):
    """008's licence classes an annotation may assert (008 FR-008.59)."""

    PERMITS = "permits"
    PRIVATE_ONLY = "private_only"
    FORBIDS = "forbids"


class LicenceOrigin(StrEnum):
    CARD_DATA = "card_data"
    TAGS = "tags"
    NONE = "none"


#: Shown when a source states no licence (absent, empty or ``unknown``; FR-001.27).
LICENCE_NOT_STATED = "not stated"
