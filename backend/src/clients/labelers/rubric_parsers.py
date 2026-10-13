"""Rubric answer parsers (FR-005.16, FR-005.43; FTID 005 section 3.4).

Each parser returns a :class:`ParsedAnswer` or raises :class:`ParseFailure`. NO parser has a
fallback branch that returns a verdict: an answer that does not parse is a parse failure, counted
and shown with its raw output, never turned into a verdict.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import jsonschema


class ParseFailure(ValueError):
    """The answer does not parse under the rubric's parser."""


@dataclass(frozen=True)
class ParsedAnswer:
    verdict: str
    score: float | None
    rationale: str | None


_VERDICT_LINE = re.compile(r"^VERDICT:\s*(?P<value>.+?)\s*$")


def verdict_line_v1(
    text: str, allowed: Sequence[str], schema: dict[str, Any] | None = None
) -> ParsedAnswer:
    """The LAST non-empty line must read ``VERDICT: <value>`` with an allowed value."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        raise ParseFailure("the answer is empty")
    match = _VERDICT_LINE.match(lines[-1])
    if match is None:
        raise ParseFailure("the last line is not 'VERDICT: <value>'")
    value = match.group("value")
    if value not in allowed:
        raise ParseFailure(f"verdict {value!r} is not one of {list(allowed)}")
    rationale = "\n".join(lines[:-1]) or None
    return ParsedAnswer(value, None, rationale)


def json_v1(
    text: str, allowed: Sequence[str], schema: dict[str, Any] | None = None
) -> ParsedAnswer:
    """The whole answer is one JSON object valid under the rubric's schema, with ``verdict``."""
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        raise ParseFailure("the answer is not JSON") from None
    if not isinstance(value, dict):
        raise ParseFailure("the answer is not a JSON object")
    if schema is not None:
        try:
            jsonschema.validate(value, schema)
        except jsonschema.ValidationError as exc:
            raise ParseFailure(f"the answer breaks the schema: {exc.message}") from None
    verdict = value.get("verdict")
    if not isinstance(verdict, str) or verdict not in allowed:
        raise ParseFailure(f"verdict {verdict!r} is not one of {list(allowed)}")
    score = value.get("score")
    rationale = value.get("rationale")
    return ParsedAnswer(
        verdict,
        float(score) if isinstance(score, int | float) and not isinstance(score, bool) else None,
        rationale if isinstance(rationale, str) else None,
    )


Parser = Callable[[str, Sequence[str], dict[str, Any] | None], ParsedAnswer]

PARSERS: dict[str, Parser] = {"verdict_line_v1": verdict_line_v1, "json_v1": json_v1}
