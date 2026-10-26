"""017 slice 3 fixtures: carriers, rule-set pins and declared label rules for BOTH test behaviours.

The rows cross every declared kind x framing x form x placement of ``fixture-alpha`` (slot
carriers, multi-byte text around the slot) and every framing x planned answer of ``fixture-beta``
(instructed carriers, written whole). The chain is the production one, run through 003's real
executor: ``insert_element@1`` -> ``rule_set@1`` -> ``derive_labels@1``.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from typing import Any

import pyarrow as pa

from src.rules import rule_set
from tests.fixtures import humor_pool as hp
from tests.support.behaviour_fixtures import FIXTURES, definition, plan_document
from tests.support.curation_fixtures import Ran, run_operator

SLOT = "[[SLOT-7F3A]]"
ROLES = {"text": "content"}
#: Per kind, the element the carrier receives (a decoy kind is filled by the rule set's decoy).
ALPHA_ELEMENTS = {
    "valid": "4111 1111 1111 1111",
    "invalid": "4111 1111 1111 1112",
    "masked": "XXXX XXXX XXXX 1111",
    "lookalike": None,
}
#: Multi-byte text around the slot: CJK, an emoji and a combining accent (M3's target).
ALPHA_FRAMES = {
    "start": "{slot} は確認済み 🙂 é end",
    "middle": "Résumé 日本語 ✉️ {slot} — trailing words",
    "end": "Notes 💳 before the value: {slot}",
}


def rule_set_document(key: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / key / "rule_set.json").read_text())
    return data


def labels(key: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / key / "labels.json").read_text())
    return data


def pin(key: str, document: dict[str, Any] | None = None) -> dict[str, Any]:
    """A resolved pin as the build hands it to a step (the canonical text and its sha256)."""
    document = document or rule_set_document(key)
    text = rule_set.canonical_bytes(document).decode()
    return {
        "rule_set_name": document["name"],
        "rule_set_version": 1,
        "rule_set_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "rule_set_document": text,
    }


def vocabulary(key: str) -> str:
    return json.dumps(plan_document(key)["vocabulary"])


def alpha_carriers() -> pa.Table:
    """One slot carrier per kind x framing x form x placement (72 rows), with its element."""
    v = plan_document("fixture-alpha")["vocabulary"]
    rows = []
    for kind, framing, form, placement in itertools.product(
        v["kinds"], v["framings"], v["forms"], v["placements"]
    ):
        rows.append(
            {
                "text": ALPHA_FRAMES[placement].format(slot=SLOT) + f" ({kind}/{framing}/{form})",
                "slot": SLOT,
                "kind": kind,
                "framing": framing,
                "form": form,
                "placement": placement,
                "genre": "email",
                "length_band": "short",
                "planned_element_valid": "valid_yes" if kind == "valid" else "valid_no",
                "value": ALPHA_ELEMENTS[kind],
            }
        )
    return hp.with_system_columns(rows, source_id="src-alpha", content=("text",))


#: fixture-beta's instructed carriers: the generator answered with (or without) a cue word.
BETA_TEXTS = {
    "complied_yes": "Here it is: the quorbish you asked about, described in full.",
    "complied_no": "I would rather not go into that topic today.",
}


def beta_carriers(*, contradict: frozenset[str] = frozenset()) -> pa.Table:
    """One instructed carrier per framing x planned answers (24 rows). A framing in
    ``contradict`` gets the OPPOSITE text of its planned ``complied`` answer (a generator that
    wrote against its instruction)."""
    v = plan_document("fixture-beta")["vocabulary"]
    complied, warned = (sq["values"] for sq in v["sub_questions"])
    rows = []
    for framing, c, w in itertools.product(v["framings"], complied, warned):
        written = c if framing not in contradict else next(x for x in complied if x != c)
        rows.append(
            {
                "text": BETA_TEXTS[written] + f" [{framing}/{c}/{w}]",
                "slot": None,
                "kind": "request",
                "framing": framing,
                "form": "prose",
                "placement": "whole",
                "genre": "memo",
                "length_band": "brief",
                "planned_complied": c,
                "planned_warned": w,
                "value": None,
            }
        )
    return hp.with_system_columns(rows, source_id="src-beta", content=("text",))


def insert_params(key: str, *, pinned: bool = True, **over: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "mode": "slot",
        "element_column": "value",
        "vocabulary": vocabulary(key),
        **(pin(key) if pinned else {}),
    }
    params.update(over)
    return params


def rule_set_params(key: str, *, use: str = "label") -> dict[str, Any]:
    return {
        **pin(key),
        "use": use,
        "text_column": "text",
        "output_prefix": labels(key)["output_prefix"],
    }


def label_params(key: str, *, pinned: bool = True, **over: Any) -> dict[str, Any]:
    doc = labels(key)
    params: dict[str, Any] = {
        "label_column": doc["label_column"],
        "vocabulary": vocabulary(key),
        "truth_tables": json.dumps(doc["truth_tables"]),
        "target_checks": json.dumps(doc["target_checks"]),
        "output_prefix": doc["output_prefix"],
        **(pin(key) if pinned else {}),
    }
    for name in ("vetoed_column", "scan_column"):
        if doc[name]:
            params[name] = doc[name]
    params.update(over)
    return {k: v for k, v in params.items() if v is not None}


def roles_after(ran: Ran) -> dict[str, str]:
    roles = dict(ROLES)
    for name in ran.output.schema.names:
        if not name.startswith("_dw_"):
            roles.setdefault(name, "metadata")
    return roles


def chain(
    key: str,
    table: pa.Table,
    *,
    insert: dict[str, Any] | None = None,
    label: dict[str, Any] | None = None,
    reports: list[dict[str, Any]] | None = None,
) -> tuple[Ran, Ran, Ran]:
    """``insert_element@1`` -> ``rule_set@1`` -> ``derive_labels@1`` over ``table``."""
    roles = {c: ("content" if c == "text" else "metadata") for c in table.schema.names}
    roles = {c: r for c, r in roles.items() if not c.startswith("_dw_")}
    inserted = run_operator(
        "insert_element", insert or insert_params(key), table, roles, report_sink=reports
    )
    facts = run_operator("rule_set", rule_set_params(key), inserted.output, roles_after(inserted))
    labelled = run_operator(
        "derive_labels", label or label_params(key), facts.output, roles_after(facts),
        report_sink=reports,
    )  # fmt: skip
    return inserted, facts, labelled


def definition_text(key: str) -> str:
    return json.dumps(definition(key))
