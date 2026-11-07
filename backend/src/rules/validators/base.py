"""The validator interface (FR-016.1 – FR-016.4; FTDD 016 section 5.4).

A validator is a registered, self-describing entry: a declaration (:class:`ValidatorDecl`), a
``check`` of one value with its neighbouring text, and a ``scan`` that returns EVERY candidate in a
text, in text order, with its span in Unicode code points and its verdict. The verdict is the
existing :class:`~src.operators.native.identifiers.schemes.Verdict`, so the format validators and
``checksum_validate@1`` cannot disagree about what "valid" means.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ...operators.native.identifiers.schemes import Verdict

__all__ = [
    "Candidate",
    "MaskShape",
    "Validator",
    "ValidatorDecl",
    "ValidatorUnknown",
    "Verdict",
]


@dataclass(frozen=True)
class MaskShape:
    """How a masked value of a format may look (FTDD 016 section 15.1; D32 starting values)."""

    #: Lengths the mask characters and kept characters together may fill.
    lengths: tuple[int, ...]
    #: Popular group layouts (group sizes) for a delimited value; an undelimited value always fits.
    layouts: tuple[tuple[int, ...], ...]
    #: Delimiters allowed between groups.
    delimiters: str
    #: Most characters a masked value may keep at its start, and at its end.
    keep_leading: int
    keep_trailing: int


@dataclass(frozen=True, eq=False)
class ValidatorDecl:
    """What a validator declares about itself (FR-016.1). ``superseded_by`` is NOT here: the
    registry derives it from the next registered version of the same id."""

    #: A format or algorithm name, never a behaviour key.
    id: str
    version: int
    description: str
    #: JSON Schema of the ``params`` a rule passes.
    params_schema: dict[str, Any]
    #: Every ``reason`` ``check`` can return (pinned by the declared-reasons test).
    reasons: tuple[str, ...]
    #: ``(name, type)`` of each output a candidate carries.
    outputs: tuple[tuple[str, str], ...]
    #: The algorithms it applies.
    algorithms: tuple[str, ...]
    #: True only where a valid value is a published identifier of an institution (D30).
    institution_identifier: bool = False
    #: True when ``valid`` means "this is an identifier of the format"; a valid-bearing case of
    #: such a validator must carry a public source (FR-016.8). ``masked@1``'s ``valid`` means
    #: "reads masked" and bears no value.
    value_bearing: bool = True
    #: The declared issuance rule under which a value reads unissuable; None for every v1
    #: validator (FTDD 016 section 5.7: every v1 issuance rule is part of validity).
    issuance_rule: str | None = None
    #: Format validators only: the masked shape ``masked@1`` checks (FTDD 016 section 15.1).
    mask_shape: MaskShape | None = field(default=None)

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"


@dataclass(frozen=True)
class Candidate:
    """One candidate of a scan: ``[start, end)`` in code points of the scanned text."""

    start: int
    end: int
    verdict: Verdict


@runtime_checkable
class Validator(Protocol):
    decl: ValidatorDecl

    def check(self, value: str, before: str, after: str, params: Mapping[str, Any]) -> Verdict:
        """The verdict on ``value``; ``before``/``after`` are the neighbouring text."""
        ...

    def scan(self, text: str, params: Mapping[str, Any]) -> list[Candidate]:
        """Every candidate in ``text``, in text order."""
        ...


class ValidatorUnknown(LookupError):
    """A rule named a validator version that is not registered."""

    def __init__(self, validator_id: str, version: int) -> None:
        super().__init__(f"validator {validator_id}@{version} is not registered")
        self.validator_id = validator_id
        self.version = version


def output_value(verdict: Verdict, name: str) -> Any:
    """The named output of a verdict (``valid``, ``reason``, ``tier``, ``layout``, ``normalised``,
    ``detail``)."""
    return getattr(verdict, name)
