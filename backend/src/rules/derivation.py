"""``sequential_cover@1``: a draft rule set derived from labelled rows (FR-016.29 - FR-016.32;
FTDD 016 section 8.5; FTID 016 section 3.9). Pure: arrays in, a draft document and figures out; no
database, settings, clock or randomness (FR-016.4's guarantee covers this module too).

- **Split (FR-016.30).** A row's GROUP goes to held-out when ``sha256(f"{seed}\\x1f{group}")`` read as
  an integer, modulo 10,000, is below ``held_out_fraction x 10,000``. Grouped and deterministic: no
  group is on both sides, and held-out rows are never used to choose a rule.
- **Literals.** Tokens are ASCII word runs ``[0-9A-Za-z_]+``, lower-cased; candidates are their 1-,
  2- and 3-grams. Repeat: among n-grams that occur in at least ``min_support`` remaining derivation
  rows and whose precision there (positive rows / rows containing it) is at least ``floor``, take the
  highest precision, then the higher support, then the smaller n-gram text; remove the rows it
  covers; stop at ``max_signals`` or when none qualifies. Text in other scripts yields no literal,
  and the result says "low coverage" rather than pretending.
- **The draft.** Each n-gram is a ``pattern`` signal ``\\b`` + tokens joined by ``\\s+`` + ``\\b``,
  ``ignore_case: true``; the single output ``derived`` fires on ``any`` of them. Its cases are
  CONSTRUCTED from the chosen n-grams (the n-gram's own text fires; the empty text does not), never
  copied from row text, because a draft may travel in a public build (FTID I13).
- **Measurement uses the artefact.** Held-out figures come from running the DRAFT DOCUMENT through
  :func:`compose.evaluate` (the evaluator builds use), not the tokeniser.
- **States.** Fewer held-out rows than ``min_held_out_per_class`` in either class: ``not_measured``,
  every figure null. No qualifying literal, or a draft below the floor on held-out precision:
  ``low_coverage`` with its figures. Otherwise ``measured``.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from . import dialect, rule_set
from .compose import CompiledRuleSet

METHOD = "sequential_cover"
METHOD_VERSION = 1
OUTPUT = "derived"
EXPRESSION = "any_derived"
_WORD = re.compile(r"[0-9A-Za-z_]+")


@dataclass(frozen=True)
class Params:
    floor: float
    max_signals: int
    min_support: int
    held_out_fraction: float
    seed: int
    min_held_out_per_class: int


@dataclass
class Derived:
    state: str
    document: dict[str, Any] | None
    result: dict[str, Any] = field(default_factory=dict)


def held_out(group: str, seed: int, fraction: float) -> bool:
    digest = hashlib.sha256(f"{seed}\x1f{group}".encode()).hexdigest()
    return int(digest, 16) % 10_000 < fraction * 10_000


def ngrams(text: str) -> set[str]:
    tokens = [t.lower() for t in _WORD.findall(text or "")]
    out: set[str] = set()
    for n in (1, 2, 3):
        for i in range(len(tokens) - n + 1):
            out.add(" ".join(tokens[i : i + n]))
    return out


def choose(
    grams: Sequence[set[str]],
    labels: Sequence[bool],
    params: Params,
    cancel: Callable[[], None] | None = None,
) -> list[dict[str, Any]]:
    """The chosen literals in order, each with its support and precision on derivation rows."""
    remaining = set(range(len(grams)))
    chosen: list[dict[str, Any]] = []
    while len(chosen) < params.max_signals and remaining:
        if cancel is not None:
            cancel()
        support: Counter[str] = Counter()
        positive: Counter[str] = Counter()
        for i in remaining:
            for gram in grams[i]:
                support[gram] += 1
                if labels[i]:
                    positive[gram] += 1
        best: tuple[float, int, str] | None = None
        for gram, count in support.items():
            if count < params.min_support:
                continue
            precision = positive[gram] / count
            if precision < params.floor:
                continue
            key = (-precision, -count, gram)
            if best is None or key < best:
                best = key
        if best is None:
            break
        gram = best[2]
        chosen.append({"literal": gram, "support": -best[1], "precision": -best[0]})
        remaining = {i for i in remaining if gram not in grams[i]}
    return chosen


def draft_document(
    literals: Sequence[str],
    *,
    name: str,
    behaviour_key: str,
    provenance: dict[str, Any],
    label_column: str,
) -> dict[str, Any]:
    signals = [
        {
            "id": f"d{n}",
            "kind": "pattern",
            "pattern": r"\b"
            + r"\s+".join(dialect.escape_literal(t) for t in lit.split(" "))
            + r"\b",
            "ignore_case": True,
            "fold_fullwidth_ascii": False,
            "reads": "text",
        }
        for n, lit in enumerate(literals, start=1)
    ]
    return {
        "format": rule_set.FORMAT,
        "name": name,
        "behaviour_key": behaviour_key,
        "description": f"Derived by {METHOD}@{METHOD_VERSION} from label column {label_column}: "
        f"{len(literals)} literal(s).",
        "signals": signals,
        "expressions": {EXPRESSION: {"any": [{"signal": s["id"]} for s in signals]}},
        "outputs": [{"column": OUTPUT, "type": "bool", "value": {"fired": EXPRESSION}}],
        "cases": [
            {
                "id": "derived.fires",
                "input": {"text": literals[0]},
                "expect": {"outputs": {OUTPUT: True}, "vetoed": False},
                "source": {"kind": "constructed"},
                "note": "the first literal's own text (constructed, never row text)",
            },
            {
                "id": "derived.quiet",
                "input": {"text": ""},
                "expect": {"outputs": {OUTPUT: False}, "vetoed": False},
                "source": {"kind": "constructed"},
                "note": "the empty text fires nothing",
            },
        ],
        "provenance": provenance,
    }


def _figures(fired: Sequence[bool], labels: Sequence[bool]) -> dict[str, float | int | None]:
    tp = sum(1 for f, y in zip(fired, labels, strict=True) if f and y)
    fp = sum(1 for f, y in zip(fired, labels, strict=True) if f and not y)
    positives = sum(1 for y in labels if y)
    n = len(labels)
    return {
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / positives if positives else None,
        "coverage": (tp + fp) / n if n else None,
        "fired": tp + fp,
    }


def _empty_figures() -> dict[str, None]:
    return {"precision": None, "recall": None, "coverage": None, "fired": None}


def rules_digest(document: dict[str, Any]) -> str:
    """FR-016.32's reproducibility hash: the draft without what names THIS run (its
    ``provenance`` carries the derivation id). Same source rows, parameters and seed: same hash."""
    return rule_set.digest({k: v for k, v in document.items() if k != "provenance"})


def sequential_cover(
    texts: Sequence[str],
    labels: Sequence[bool],
    groups: Sequence[str],
    params: Params,
    *,
    name: str,
    behaviour_key: str,
    provenance: dict[str, Any],
    label_column: str,
    cancel: Callable[[], None] | None = None,
) -> Derived:
    """Derive, then measure the draft on held-out rows only."""
    side = [held_out(g, params.seed, params.held_out_fraction) for g in groups]
    derive = [i for i, h in enumerate(side) if not h]
    hold = [i for i, h in enumerate(side) if h]
    grams = [ngrams(texts[i]) for i in derive]
    chosen = choose(grams, [labels[i] for i in derive], params, cancel)
    literals = [c["literal"] for c in chosen]
    every = [ngrams(t) for t in texts]
    literal_counts = {lit: sum(1 for g in every if lit in g) for lit in literals}
    per_class = {
        "positive": sum(1 for i in hold if labels[i]),
        "negative": sum(1 for i in hold if not labels[i]),
    }
    document = (
        draft_document(
            literals,
            name=name,
            behaviour_key=behaviour_key,
            provenance=provenance,
            label_column=label_column,
        )
        if literals
        else None
    )
    result: dict[str, Any] = {
        "method": METHOD,
        "method_version": METHOD_VERSION,
        "seed": params.seed,
        "rows": {"derivation": len(derive), "held_out": len(hold)},
        "groups": {
            "derivation": len({groups[i] for i in derive}),
            "held_out": len({groups[i] for i in hold}),
            "on_both_sides": len({groups[i] for i in derive} & {groups[i] for i in hold}),
        },
        "held_out_per_class": per_class,
        "literals": chosen,
        "literal_counts": literal_counts,
        "limitation": "Literals are ASCII word n-grams; text in other scripts yields low coverage.",
    }
    measured = min(per_class.values()) >= params.min_held_out_per_class
    if document is not None:
        result["rules_sha256"] = rules_digest(document)
    if not measured:
        result["draft"] = _empty_figures()
        result["signals"] = {f"d{n}": _empty_figures() for n in range(1, len(literals) + 1)}
        return Derived("not_measured", document, result)
    if document is None:
        result["draft"] = _empty_figures()
        result["signals"] = {}
        return Derived("low_coverage", None, result)
    compiled = CompiledRuleSet(rule_set.parse(document))
    y = [labels[i] for i in hold]
    rows = [compiled.evaluate(texts[i]) for i in hold]
    result["draft"] = _figures([bool(r.outputs[OUTPUT]) for r in rows], y)
    result["signals"] = {
        sid: _figures([bool(r.signals[sid]["fired"]) for r in rows], y)
        for sid in (f"d{n}" for n in range(1, len(literals) + 1))
    }
    precision = result["draft"]["precision"]
    state = "measured" if precision is not None and precision >= params.floor else "low_coverage"
    return Derived(state, document, result)
