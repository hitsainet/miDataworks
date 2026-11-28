"""Second-opinion strata (017 FR-017.38, FR-017.41, FR-017.43; FTID §3.8; TD-017.22, TD-017.26).

Pure helpers, no database:

- :func:`instructed_params`: 017's step resolver for ``sample``. A stratified step that names an
  instructed column and not its rate or minimum runs with ``SECOND_OPINION_INSTRUCTED_RATE`` /
  ``_MIN`` as in force when the build starts, so the step's identity records the value it ran with
  (and a changed setting is a changed step). Any other ``sample`` step is unchanged.
- :func:`stratum_of`: a row's stratum label (the stratified sample, the agreement and the review
  hand-off name strata the same way).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

STRATIFIED = "stratified"
#: FR-017.43's default strata, read where the version has them.
DEFAULT_STRATA: tuple[str, ...] = ("genre", "generator", "kind", "form")


def instructed_params(params: Mapping[str, Any], lookup: Any = None) -> dict[str, Any]:
    from ...core.config import get_settings

    out = dict(params)
    if out.get("mode") != STRATIFIED or out.get("instructed_column") is None:
        return out
    settings = get_settings()
    if out.get("instructed_rate") is None:
        out["instructed_rate"] = float(settings.second_opinion_instructed_rate)
    if out.get("instructed_min") is None:
        out["instructed_min"] = int(settings.second_opinion_instructed_min)
    return out


def stratum_of(row: Mapping[str, Any], by: Sequence[str]) -> str:
    """``genre=email | kind=valid``: a null is its own value, written ``None``."""
    return " | ".join(f"{c}={row.get(c)}" for c in by)
