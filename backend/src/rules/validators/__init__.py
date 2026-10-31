"""THE validator registry (FR-016.1, FR-016.2; FTID 016 section 3.1).

``VALIDATORS`` is the one list, keyed by the exact ``(id, version)``. Adding a validator, or a new
version of one, is a reviewed code change that ships with its cases (FR-016.7's coverage test reads
this dict). A rule names an exact version and :func:`resolve` returns exactly that version: it never
resolves "the latest" (mutation M13). ``superseded_by`` is derived here from the next registered
version of the same id, never typed into a declaration.
"""

from __future__ import annotations

from typing import Any

from .algorithms import ALGORITHM_VALIDATORS
from .base import Candidate, Validator, ValidatorDecl, ValidatorUnknown
from .formats import FORMAT_VALIDATORS
from .masked import MASKED

__all__ = [
    "VALIDATORS",
    "Candidate",
    "Validator",
    "ValidatorDecl",
    "ValidatorUnknown",
    "declaration",
    "resolve",
    "superseded_by",
]

_ALL: tuple[Validator, ...] = (*FORMAT_VALIDATORS, *ALGORITHM_VALIDATORS, MASKED)
VALIDATORS: dict[tuple[str, int], Validator] = {(v.decl.id, v.decl.version): v for v in _ALL}


def resolve(validator_id: str, version: int) -> Validator:
    """The validator ``validator_id@version`` exactly; refuses an unregistered version."""
    found = VALIDATORS.get((validator_id, int(version)))
    if found is None:
        raise ValidatorUnknown(validator_id, int(version))
    return found


def superseded_by(validator_id: str, version: int) -> int | None:
    """The next registered version of the same id, or None when this is the newest."""
    later = [v for (i, v) in VALIDATORS if i == validator_id and v > version]
    return min(later) if later else None


def declaration(validator: Validator) -> dict[str, Any]:
    """The JSON form of a declaration, as the validator routes return it."""
    decl = validator.decl
    shape = decl.mask_shape
    return {
        "id": decl.id,
        "version": decl.version,
        "ref": decl.ref,
        "description": decl.description,
        "params_schema": decl.params_schema,
        "reasons": list(decl.reasons),
        "outputs": [{"name": n, "type": t} for n, t in decl.outputs],
        "algorithms": list(decl.algorithms),
        "institution_identifier": decl.institution_identifier,
        "value_bearing": decl.value_bearing,
        "issuance_rule": decl.issuance_rule,
        "mask_shape": (
            None
            if shape is None
            else {
                "lengths": list(shape.lengths),
                "layouts": [list(g) for g in shape.layouts],
                "delimiters": shape.delimiters,
                "keep_leading": shape.keep_leading,
                "keep_trailing": shape.keep_trailing,
            }
        ),
        "superseded_by": superseded_by(decl.id, decl.version),
    }
