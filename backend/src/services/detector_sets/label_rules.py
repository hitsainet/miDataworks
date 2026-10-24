"""Label-mapping rules (FR-009.5; mirrors miStudio ``ProbeDatasetCreate`` validators).

``check_mapping`` returns problems and never raises. Values are compared as STRINGS, because
miStudio compares the raw value as a string (``schemas/probe_monitor.py:98-99``): a label column of
integers ``1``/``0`` maps through the keys ``"1"``/``"0"``.

Problems:

- ``unmapped_value``: a value present in the split has no mapping (miStudio would refuse), a
  NULL label included: nulls are counted under ``"None"`` (``NULL_KEY``), as miStudio names them;
- ``unknown_target``: a mapping target is not positive, negative or excluded;
- ``no_positive`` / ``no_negative``: a train, in-distribution or OOD role would register with one
  class (miStudio cannot score it);
- ``positive_in_calibration``: calibration negatives map a value to positive (miStudio refuses).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

TARGETS = frozenset({"positive", "negative", "excluded"})


#: How a NULL label value is named in a mapping. miStudio's ``map_label`` reads a null label as
#: ``mapping.get("None") or mapping.get("null")`` (``services/probe_monitor_inputs.py``), and
#: ``str(None)`` is ``"None"``: so the value is counted under ``"None"`` and either key maps it.
NULL_KEY = "None"
NULL_ALIASES: frozenset[str] = frozenset({"None", "null"})


def value_key(value: object) -> str:
    """A raw label value as miStudio compares it: booleans in lower case, like JSON."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def target_for(value: str, mapping: Mapping[str, str]) -> str | None:
    """The mapping's target for a label value keyed by ``value_key``, as miStudio resolves it.

    A null value (``NULL_KEY``) is looked up as ``"None"``, then ``"null"`` (miStudio's
    ``map_label``); any other value by its key. None means unmapped: miStudio counts such a row as
    unparseable."""
    normalised = {value_key(k): v for k, v in mapping.items()}
    if value == NULL_KEY:
        return normalised.get(NULL_KEY) or normalised.get("null")
    return normalised.get(value)


def mapping_key(key: object) -> str:
    """A mapping key as the value it maps: ``"null"`` and ``"None"`` both name a null label."""
    text = value_key(key)
    return NULL_KEY if text in NULL_ALIASES else text


@dataclass(frozen=True)
class MappingProblem:
    code: str
    value: str | None
    message: str


def check_mapping(
    role: str, value_counts: Mapping[str, int], mapping: Mapping[str, str]
) -> list[MappingProblem]:
    """Problems with ``mapping`` over the split's distinct label values and their row counts."""
    problems: list[MappingProblem] = []
    present = {value_key(v): int(n) for v, n in value_counts.items() if int(n) > 0}
    normalised = {value_key(k): v for k, v in mapping.items()}
    for key, target in sorted(normalised.items()):
        if target not in TARGETS:
            problems.append(
                MappingProblem(
                    "unknown_target",
                    key,
                    f"Label value {key!r} maps to {target!r}; use positive, negative or excluded.",
                )
            )
    for key in sorted(present):
        if target_for(key, mapping) is None:
            problems.append(
                MappingProblem(
                    "unmapped_value",
                    key,
                    (
                        f"Null label values appear in the split ({present[key]:,} rows) and have "
                        f"no mapping; miStudio would count them unparseable. Map the key "
                        f"{NULL_KEY!r} to positive, negative or excluded."
                        if key == NULL_KEY
                        else f"Label value {key!r} appears in the split ({present[key]:,} rows) "
                        "and has no mapping. Map it to positive, negative or excluded."
                    ),
                )
            )
    positives = sum(n for k, n in present.items() if target_for(k, mapping) == "positive")
    negatives = sum(n for k, n in present.items() if target_for(k, mapping) == "negative")
    if role == "calibration_negatives":
        mapped_positive = sorted(k for k, t in normalised.items() if t == "positive")
        if mapped_positive:
            problems.append(
                MappingProblem(
                    "positive_in_calibration",
                    mapped_positive[0],
                    "Calibration negatives must map no value to positive; miStudio refuses it. "
                    "Map these values to negative or excluded.",
                )
            )
        return problems
    if positives == 0:
        problems.append(
            MappingProblem(
                "no_positive",
                None,
                "No row in this split maps to positive. Map the positive class's value to positive.",
            )
        )
    if negatives == 0:
        problems.append(
            MappingProblem(
                "no_negative",
                None,
                "No row in this split maps to negative. Map the negative class's value to negative.",
            )
        )
    return problems


def expected_counts(value_counts: Mapping[str, int], mapping: Mapping[str, str]) -> dict[str, int]:
    """Rows per class under the mapping: what miStudio's ``counts`` must report (FR-009.24)."""
    out = {"positive": 0, "negative": 0, "excluded": 0}
    for value, n in value_counts.items():
        target = target_for(value_key(value), mapping)
        if target in out:
            out[target] += int(n)
    return out
