"""A behaviour's vocabulary and planned shares (017 FR-017.13, FR-017.54, FR-017.55; FTID §3.3).

The one place a vocabulary is parsed and capped: every operator that reads one calls
:func:`parse_vocabulary` and :func:`parse_shares`. No code holds a list of genres, framings, kinds,
forms, length bands, sub-questions or phrases; they are the behaviour's data, declared in the plan
step's parameters (as JSON text: the operator schema subset has no nested objects, FTID I14,
spike 0.6). Only the five ``placement`` values are fixed, by FR-017.12.

Refusals name what to fix: ``vocabulary_invalid`` (the field and the rule), ``vocabulary_value_
undeclared`` (the value and the field it appeared in), ``cap_exceeded`` (the setting
``BEHAVIOUR_MAX_VOCABULARY_VALUES``, its limit and the field). Each carries ``details.next``.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ...core.config import get_settings

#: FR-017.12 fixes these values (``whole``: an instructed carrier written as one text).
PLACEMENTS: tuple[str, ...] = ("start", "middle", "end", "none", "whole")
DEFAULT_PLACEMENTS: tuple[str, ...] = ("start", "middle", "end")
CAP_SETTING = "BEHAVIOUR_MAX_VOCABULARY_VALUES"
#: FR-017.29, FR-017.54: the most sub-questions one vocabulary declares.
SUBQUESTION_CAP_SETTING = "BEHAVIOUR_MAX_SUBQUESTIONS"
#: A sub-question names the ``planned_<name>`` and ``sq_<name>`` columns.
SUB_QUESTION_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
#: The plan's census dimensions (FR-017.17), then the dimensions drawn on their own.
CELL_DIMENSIONS: tuple[str, ...] = ("genre", "placement", "framing", "length_band")
OTHER_DIMENSIONS: tuple[str, ...] = ("kind", "form")
SHARE_TOLERANCE = 1e-6
#: The most (genre, placement, framing, length band) cells a plan may make: ``rows``' maximum.
MAX_CELLS = 100_000
NEXT_VALIDATE = {
    "tool": "dataworks_validate_recipe",
    "args": {},
    "why": "fix the plan step's vocabulary or shares, then validate the recipe again",
}


class VocabularyError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = {**(details or {}), "next": NEXT_VALIDATE}
        if code == "cap_exceeded":
            self.details["next"] = {
                "tool": "dataworks_get_settings",
                "args": {},
                "why": f"read {self.details.get('setting') or CAP_SETTING}",
            }

    def problem(self, pointer: str) -> str:
        """The registry's ``check_params`` form: ``<pointer>: <code>: <message>``."""
        return f"{pointer}: {self.code}: {self.message}"


@dataclass(frozen=True)
class LengthBand:
    name: str
    min_chars: int
    max_chars: int | None

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "min_chars": self.min_chars, "max_chars": self.max_chars}


@dataclass(frozen=True)
class SubQuestion:
    name: str
    values: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "values": list(self.values)}


@dataclass(frozen=True)
class Vocabulary:
    genres: tuple[str, ...]
    framings: tuple[str, ...]
    kinds: tuple[str, ...]
    forms: tuple[str, ...]
    placements: tuple[str, ...]
    length_bands: tuple[LengthBand, ...]
    sub_questions: tuple[SubQuestion, ...]
    phrase_list: tuple[str, ...]
    decoys: tuple[str, ...]

    def values(self, dimension: str) -> tuple[str, ...]:
        """The declared values of a share dimension (``sq:<name>`` for a sub-question)."""
        if dimension.startswith("sq:"):
            name = dimension[3:]
            for sq in self.sub_questions:
                if sq.name == name:
                    return sq.values
            raise VocabularyError(
                "vocabulary_value_undeclared",
                f"Shares name sub-question {name!r}, which the vocabulary does not declare.",
                {"field": "shares", "value": name},
            )
        by_dimension = {
            "genre": self.genres,
            "framing": self.framings,
            "kind": self.kinds,
            "form": self.forms,
            "placement": self.placements,
            "length_band": tuple(b.name for b in self.length_bands),
        }
        if dimension not in by_dimension:
            raise VocabularyError(
                "vocabulary_value_undeclared",
                f"Shares name dimension {dimension!r}; the dimensions are "
                f"{sorted(by_dimension)} and sq:<sub-question>.",
                {"field": "shares", "value": dimension},
            )
        return by_dimension[dimension]

    def dimensions(self) -> tuple[str, ...]:
        return (
            *CELL_DIMENSIONS,
            *OTHER_DIMENSIONS,
            *(f"sq:{sq.name}" for sq in self.sub_questions),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "genres": list(self.genres),
            "framings": list(self.framings),
            "kinds": list(self.kinds),
            "forms": list(self.forms),
            "placements": list(self.placements),
            "length_bands": [b.as_dict() for b in self.length_bands],
            "sub_questions": [sq.as_dict() for sq in self.sub_questions],
            "phrase_list": list(self.phrase_list),
            "decoys": list(self.decoys),
        }


def _json(text: Any, field: str) -> Any:
    if isinstance(text, (dict, list)):
        return text
    try:
        return json.loads(str(text))
    except ValueError as exc:
        raise VocabularyError(
            "vocabulary_invalid", f"{field} is not JSON: {exc}.", {"field": field}
        ) from None


def _strings(raw: Mapping[str, Any], field: str, *, required: bool, cap: int) -> tuple[str, ...]:
    value = raw.get(field)
    if value is None:
        if required:
            raise VocabularyError(
                "vocabulary_invalid",
                f"The vocabulary declares no {field}; list at least one value.",
                {"field": f"vocabulary.{field}"},
            )
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise VocabularyError(
            "vocabulary_invalid",
            f"vocabulary.{field} must be a list of non-empty strings.",
            {"field": f"vocabulary.{field}"},
        )
    if required and not value:
        raise VocabularyError(
            "vocabulary_invalid",
            f"The vocabulary declares no {field}; list at least one value.",
            {"field": f"vocabulary.{field}"},
        )
    duplicates = sorted({v for v in value if value.count(v) > 1})
    if duplicates:
        raise VocabularyError(
            "vocabulary_invalid",
            f"vocabulary.{field} lists {duplicates} more than once.",
            {"field": f"vocabulary.{field}", "values": duplicates},
        )
    _cap(len(value), cap, f"vocabulary.{field}")
    return tuple(value)


def _cap(actual: int, cap: int, field: str) -> None:
    if actual > cap:
        raise VocabularyError(
            "cap_exceeded",
            f"{field} declares {actual} values; {CAP_SETTING} allows {cap}.",
            {"setting": CAP_SETTING, "limit": cap, "actual": actual, "field": field},
        )


def _bands(raw: Mapping[str, Any], cap: int) -> tuple[LengthBand, ...]:
    value = raw.get("length_bands")
    if not isinstance(value, list) or not value:
        raise VocabularyError(
            "vocabulary_invalid",
            "The vocabulary declares no length_bands; list at least one "
            '{"name", "min_chars", "max_chars"} (max_chars null: no upper bound).',
            {"field": "vocabulary.length_bands"},
        )
    _cap(len(value), cap, "vocabulary.length_bands")
    out: list[LengthBand] = []
    for item in value:
        if not isinstance(item, dict) or set(item) - {"name", "min_chars", "max_chars"}:
            raise VocabularyError(
                "vocabulary_invalid",
                "Each length band is {name, min_chars, max_chars}.",
                {"field": "vocabulary.length_bands"},
            )
        name, low, high = item.get("name"), item.get("min_chars", 0), item.get("max_chars")
        if not isinstance(name, str) or not name.strip():
            raise VocabularyError(
                "vocabulary_invalid",
                "A length band needs a name.",
                {"field": "vocabulary.length_bands"},
            )
        if (
            isinstance(low, bool)
            or not isinstance(low, int)
            or low < 0
            or (high is not None and (isinstance(high, bool) or not isinstance(high, int)))
            or (high is not None and high <= low)
        ):
            raise VocabularyError(
                "vocabulary_invalid",
                f"Length band {name!r}: min_chars is a whole number >= 0 and max_chars a larger "
                "whole number or null (code points).",
                {"field": "vocabulary.length_bands", "value": name},
            )
        out.append(LengthBand(name, low, high))
    names = [b.name for b in out]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise VocabularyError(
            "vocabulary_invalid",
            f"vocabulary.length_bands names {duplicates} more than once.",
            {"field": "vocabulary.length_bands", "values": duplicates},
        )
    return tuple(out)


def _sub_questions(raw: Mapping[str, Any], cap: int) -> tuple[SubQuestion, ...]:
    value = raw.get("sub_questions") or []
    if not isinstance(value, list):
        raise VocabularyError(
            "vocabulary_invalid",
            "vocabulary.sub_questions is a list of {name, values}.",
            {"field": "vocabulary.sub_questions"},
        )
    out: list[SubQuestion] = []
    for item in value:
        if not isinstance(item, dict) or set(item) - {"name", "values"}:
            raise VocabularyError(
                "vocabulary_invalid",
                "Each sub-question is {name, values}.",
                {"field": "vocabulary.sub_questions"},
            )
        name = item.get("name")
        if not isinstance(name, str) or not SUB_QUESTION_PATTERN.fullmatch(name):
            raise VocabularyError(
                "vocabulary_invalid",
                f"Sub-question name {name!r} must match {SUB_QUESTION_PATTERN.pattern} (it names "
                "the planned_<name> and sq_<name> columns).",
                {"field": "vocabulary.sub_questions", "value": name},
            )
        values = _strings(item, "values", required=True, cap=cap)
        if len(values) < 2:
            raise VocabularyError(
                "vocabulary_invalid",
                f"Sub-question {name!r} needs at least two answers.",
                {"field": "vocabulary.sub_questions", "value": name},
            )
        out.append(SubQuestion(name, values))
    sq_cap = int(get_settings().behaviour_max_subquestions)
    if len(out) > sq_cap:
        raise VocabularyError(
            "cap_exceeded",
            f"vocabulary.sub_questions declares {len(out)} sub-questions; "
            f"{SUBQUESTION_CAP_SETTING} allows {sq_cap}.",
            {
                "setting": SUBQUESTION_CAP_SETTING,
                "limit": sq_cap,
                "actual": len(out),
                "field": "vocabulary.sub_questions",
            },
        )
    names = [sq.name for sq in out]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise VocabularyError(
            "vocabulary_invalid",
            f"vocabulary.sub_questions names {duplicates} more than once.",
            {"field": "vocabulary.sub_questions", "values": duplicates},
        )
    return tuple(out)


ALLOWED_FIELDS = frozenset(
    {
        "genres",
        "framings",
        "kinds",
        "forms",
        "placements",
        "length_bands",
        "sub_questions",
        "phrase_list",
        "decoys",
    }
)


def parse_vocabulary(text: Any) -> Vocabulary:
    """The declared vocabulary, or a refusal naming the field (FR-017.13)."""
    raw = _json(text, "vocabulary")
    if not isinstance(raw, dict):
        raise VocabularyError(
            "vocabulary_invalid", "vocabulary is a JSON object.", {"field": "vocabulary"}
        )
    unknown = sorted(set(raw) - ALLOWED_FIELDS)
    if unknown:
        raise VocabularyError(
            "vocabulary_invalid",
            f"vocabulary has unknown field(s) {unknown}; the fields are "
            f"{sorted(ALLOWED_FIELDS)}.",
            {"field": "vocabulary", "values": unknown},
        )
    cap = int(get_settings().behaviour_max_vocabulary_values)
    placements = _strings(raw, "placements", required=False, cap=cap) or DEFAULT_PLACEMENTS
    for value in placements:
        if value not in PLACEMENTS:
            raise VocabularyError(
                "vocabulary_value_undeclared",
                f"Placement {value!r} is not one of {list(PLACEMENTS)} (FR-017.12).",
                {"field": "vocabulary.placements", "value": value},
            )
    return Vocabulary(
        genres=_strings(raw, "genres", required=True, cap=cap),
        framings=_strings(raw, "framings", required=True, cap=cap),
        kinds=_strings(raw, "kinds", required=True, cap=cap),
        forms=_strings(raw, "forms", required=True, cap=cap),
        placements=placements,
        length_bands=_bands(raw, cap),
        sub_questions=_sub_questions(raw, cap),
        phrase_list=_strings(raw, "phrase_list", required=False, cap=cap),
        decoys=_strings(raw, "decoys", required=False, cap=cap),
    )


def parse_shares(text: Any, vocabulary: Vocabulary) -> dict[str, dict[str, float]]:
    """Planned shares per dimension, every declared value present (a dimension the shares omit is
    uniform). A value the vocabulary does not declare is refused naming it and its field."""
    raw = _json(text if text not in (None, "") else "{}", "shares")
    if not isinstance(raw, dict):
        raise VocabularyError("vocabulary_invalid", "shares is a JSON object.", {"field": "shares"})
    out: dict[str, dict[str, float]] = {}
    for dimension in vocabulary.dimensions():
        declared = vocabulary.values(dimension)
        given = raw.get(dimension)
        if given is None:
            out[dimension] = {v: 1.0 / len(declared) for v in declared}
            continue
        if not isinstance(given, dict):
            raise VocabularyError(
                "vocabulary_invalid",
                f"shares.{dimension} is an object of value -> share.",
                {"field": f"shares.{dimension}"},
            )
        for value in given:
            if value not in declared:
                raise VocabularyError(
                    "vocabulary_value_undeclared",
                    f"shares.{dimension} names {value!r}, which the vocabulary does not declare "
                    f"(declared: {list(declared)}).",
                    {"field": f"shares.{dimension}", "value": value},
                )
        shares: dict[str, float] = {}
        for value in declared:
            share = given.get(value, 0.0)
            if (
                isinstance(share, bool)
                or not isinstance(share, (int, float))
                or not math.isfinite(share)
                or share < 0
            ):
                raise VocabularyError(
                    "vocabulary_invalid",
                    f"shares.{dimension}.{value} must be a number >= 0.",
                    {"field": f"shares.{dimension}", "value": value},
                )
            shares[value] = float(share)
        total = sum(shares.values())
        if abs(total - 1.0) > SHARE_TOLERANCE:
            raise VocabularyError(
                "vocabulary_invalid",
                f"shares.{dimension} adds up to {total:.6f}; shares add up to 1.",
                {"field": f"shares.{dimension}", "total": total},
            )
        out[dimension] = shares
    unknown = sorted(set(raw) - set(vocabulary.dimensions()))
    for dimension in unknown:
        vocabulary.values(dimension)  # raises naming the undeclared dimension
    cells = math.prod(len(out[d]) for d in CELL_DIMENSIONS)
    if cells > MAX_CELLS:
        # Review R2: every cell is materialised (``joint_cells``) by the plan step and by every
        # descendant's census at finalize; 64 values per dimension is 16.7 million cells (several
        # GB per build). More cells than the largest plan has rows can never all be filled.
        raise VocabularyError(
            "vocabulary_invalid",
            f"The vocabulary makes {cells:,} genre x placement x framing x length band cells; "
            f"a plan may have at most {MAX_CELLS:,} (the most rows a plan holds). Declare fewer "
            "values.",
            {"limit": MAX_CELLS, "actual": cells, "field": "vocabulary"},
        )
    return out


def largest_remainder[K: (str, tuple[str, ...])](shares: Mapping[K, float], n: int) -> dict[K, int]:
    """Exactly ``n`` split by ``shares`` (Hamilton's method): floors, then the largest remainders,
    ties broken by declaration order, so the counts are deterministic and add up to ``n``."""
    exact = {k: v * n for k, v in shares.items()}
    counts = {k: int(math.floor(x)) for k, x in exact.items()}
    left = n - sum(counts.values())
    position = {k: i for i, k in enumerate(exact)}
    order = sorted(exact, key=lambda k: (-(exact[k] - counts[k]), position[k]))
    for k in order[:left]:
        counts[k] += 1
    return counts


def joint_cells(
    shares: Mapping[str, Mapping[str, float]], dimensions: Sequence[str]
) -> dict[tuple[str, ...], float]:
    """The planned share of every combination of ``dimensions`` (independent dimensions)."""
    cells: dict[tuple[str, ...], float] = {(): 1.0}
    for dimension in dimensions:
        cells = {
            (*cell, value): share * s
            for cell, share in cells.items()
            for value, s in shares[dimension].items()
        }
    return cells
