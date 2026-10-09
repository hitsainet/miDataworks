"""miLLM probe verdict -> label outcome (FR-009.49, FR-009.50, FR-009.81; P-03, P-20).

- ``verdict: true`` -> ``positive``; ``false`` -> ``negative``;
- ``verdict: null`` -> ``skipped`` with miLLM's ``not_scored_reason``: the probe said nothing, which
  is never coerced to negative (FR-009.81);
- ``provisional: true`` -> ``excluded`` with the flag kept, whatever the verdict: a provisional bar
  was cut on a different distribution, so the verdict never reaches a label (P-20).

The boundary is miLLM's: a score exactly on the threshold FIRES (``>=``, miLLM
``probe_runtime.py:665``; X-03 as corrected at Stage 3). This module never re-decides a verdict from
the score; :func:`fires` exists so a test can pin the rule against which miLLM's verdict is read.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

LabelOutcome = Literal["positive", "negative", "skipped", "excluded"]


@dataclass(frozen=True)
class VerdictLabel:
    outcome: LabelOutcome
    provisional: bool
    reason: str | None


def fires(score: float, threshold: float) -> bool:
    """miLLM's rule: a score exactly on the bar fires (P-03)."""
    return score >= threshold


def map_verdict(result: Mapping[str, Any]) -> VerdictLabel:
    """One miLLM ``/api/probes/score`` result (per input, probe and window) -> a label outcome."""
    provisional = bool(result["provisional"])
    verdict = result["verdict"]
    if provisional:
        return VerdictLabel("excluded", True, "provisional bar: cut on another distribution (P-20)")
    if verdict is None:
        return VerdictLabel("skipped", False, result.get("not_scored_reason") or "not scored")
    if verdict is True:
        return VerdictLabel("positive", False, None)
    if verdict is False:
        return VerdictLabel("negative", False, None)
    raise ValueError(f"miLLM verdict {verdict!r} is not true, false or null")
