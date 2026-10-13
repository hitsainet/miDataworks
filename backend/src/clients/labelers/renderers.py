"""Row-to-prompt rendering for templates and rubrics (FR-005.11).

A template's ``render`` is either a REGISTERED renderer name (``jev.noul_bare_v1``) or a Python
format string over the template's input fields and ``{question}``. Row text is a VALUE passed to
the format string, never part of it, so braces in a row cannot change the template.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .base import RowError
from .jev import noul_prompt

Renderer = Callable[[Mapping[str, Any], str | None], str]


def _jev_noul_bare_v1(fields: Mapping[str, Any], question: str | None) -> str:
    if question is None:
        raise RowError("the JEV template needs the run's question")
    return noul_prompt(str(fields["text"]), question)


#: Registered renderers. A template naming one of these renders through it.
RENDERERS: dict[str, Renderer] = {"jev.noul_bare_v1": _jev_noul_bare_v1}


def render(template: str, fields: Mapping[str, Any], question: str | None) -> str:
    if template in RENDERERS:
        return RENDERERS[template](fields, question)
    values = {k: ("" if v is None else str(v)) for k, v in fields.items()}
    values["question"] = question or ""
    try:
        return template.format_map(values)
    except (KeyError, IndexError, ValueError) as exc:
        raise RowError(f"the template could not be rendered: {exc}") from None
