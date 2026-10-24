"""The template check (017 FR-017.16; FTID §7.5).

A carrier template must never contain the behaviour it is used to make data for: a model copies
what it is shown (miDataworks memory ``measure-wording-before-publishing-pairs``). A template's
text (prompt and system, slot and instruction templates alike) is refused when ANY signal of the
rule sets it is checked against fires on it (016's ``rules.compose.evaluate``, the same evaluation a
build runs), or when it holds a phrase of the behaviour's declared phrase list (case-folded).
The check reads only the rule sets and phrases it is given; it names no behaviour.

Run at template save when rule sets or phrases are named, and at plan and start time for every
template of a carrier run's ``template_map`` (the rule sets the run pins, the phrase list its plan
version recorded).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...core.errors import UnprocessableError
from ...rules import compose, rule_set

CODE = "slot_template_has_element"


@dataclass(frozen=True)
class Hit:
    template: str
    rule_set: str | None
    signal: str | None
    phrase: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "template": self.template,
            "rule_set": self.rule_set,
            "signal": self.signal,
            "phrase": self.phrase,
        }


def template_texts(body: Mapping[str, Any]) -> str:
    return "\n".join(str(body.get(k) or "") for k in ("system", "prompt"))


def hits(
    templates: Mapping[str, Mapping[str, Any]],
    documents: Sequence[rule_set.RuleSetDocument],
    phrases: Sequence[str],
) -> list[Hit]:
    """Every (template, rule set, signal) that fires and every phrase found, in a fixed order."""
    found: list[Hit] = []
    for label in sorted(templates):
        text = template_texts(templates[label])
        for document in documents:
            try:
                result = compose.evaluate(document, text)
            except compose.RuleSetNotRunnable as exc:
                raise UnprocessableError(
                    f"Rule set {document.name} cannot be run here to check templates: {exc}",
                    code=exc.code,
                    details={"rule_set": document.name, **exc.details},
                ) from None
            for signal in sorted(result.signals):
                if result.signals[signal].get("fired"):
                    found.append(Hit(label, document.name, signal, None))
        folded = text.casefold()
        for phrase in phrases:
            if phrase.strip() and phrase.casefold() in folded:
                found.append(Hit(label, None, None, phrase))
    return found


def refuse(found: Sequence[Hit]) -> None:
    """``slot_template_has_element`` naming the first rule and signal (or phrase) that fired."""
    if not found:
        return
    first = found[0]
    what = (
        f"signal {first.signal!r} of rule set {first.rule_set!r} fires"
        if first.signal is not None
        else f"it holds the declared phrase {first.phrase!r}"
    )
    raise UnprocessableError(
        f"Template {first.template} cannot be used for carriers: {what} on its text. A model "
        "copies what its template shows; rewrite the template without the element.",
        code=CODE,
        details={
            "template": first.template,
            "rule_set": first.rule_set,
            "signal": first.signal,
            "phrase": first.phrase,
            "hits": [h.as_dict() for h in found[:50]],
            "hits_count": len(found),
            "next": {
                "tool": "dataworks_clone_generation_template",
                "args": {},
                "why": "save a version of the template without the element",
            },
        },
    )


async def load_documents(
    db: AsyncSession, pins: Sequence[Mapping[str, Any]]
) -> list[rule_set.RuleSetDocument]:
    """The pinned rule sets, resolved exactly as a build resolves a ``rule_set@1`` step (016: the
    stored version, its sha256 checked, its own cases re-run)."""
    if not pins:
        return []
    from ..rules import step_resolution
    from ..step_params import mapping_lookup

    steps: list[dict[str, Any]] = [
        {
            "operator": step_resolution.OPERATOR,
            "params": {
                "rule_set_name": p["name"],
                "rule_set_version": int(p["version"]),
                "rule_set_sha256": p["sha256"],
            },
        }
        for p in pins
    ]
    lookup = mapping_lookup(await step_resolution.preload(db, steps))
    out = []
    for step in steps:
        params = step_resolution.resolve_params(step["params"], lookup)
        out.append(rule_set.parse(json.loads(params[step_resolution.DOCUMENT_PARAM])))
    return out
