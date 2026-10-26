"""Public test values only (FR-016.8; D28, D30; FTDD 016 section 5.7).

The ONE authority, used by the contract-file test, the behaviour-pack test and (slice 3) check C-8.
A case is **valid-bearing** when RUNNING it produces a candidate with ``valid: true`` from a validator
whose ``valid`` means "this is an identifier" (``value_bearing``) - computed, never read from what the
author claimed. A valid-bearing case in a public file must carry:

- ``published_test_value`` with a citation of a publication that declares the value a test or
  example value; or
- ``published_reference_value``, only when every validator that read it valid declares
  ``institution_identifier`` (D30: published ABA routing numbers); or
- ``unissuable``, only when the validator's declared issuance rule reads the value unissuable. No v1
  validator declares one (every v1 issuance rule is part of validity), so every such claim on a
  valid-bearing case is a violation.

Anything else (``constructed`` included) is a violation. Whether a citation is TRUE is a review item.

**Every string (D38-2, operator 2026-10-23; FR-016.8 "any other valid-looking value").** The case
check above reads only what a case's OWN validators read. :func:`string_violations` reads every
string of a public file (keys included, notes, citations, expected outputs, identity bytes) with
every **identifier reader**: a registered, value-bearing format validator (one that declares a masked
shape: ``card_pan``, ``ssn_us``, ``aba_routing``, ``iban``, ``nhs``, ``aadhaar`` in v1, and any
format registered later). Each reader runs its all-candidates ``scan`` with no params (the same
candidates a rule's validator signal reads). A reading that is valid is a violation unless its value (ASCII letters and digits,
upper-cased) is **documented**: read valid in the input of a case of the same file whose source is a
cited ``published_test_value`` (public for every reader), or a ``published_reference_value`` (public
only for an ``institution_identifier`` reader, D30). Algorithm validators (``luhn``, ``verhoeff``,
``mod97_10``, ``weighted_mod``, ``length_charset``) are not readers: they accept any digit run with a
right check digit (offsets, hashes), and the last two need params; a value is identifier-like when an
identifier format reads it valid. Constructed values in public files must therefore be invalid for
every reader (for example SSN area 9xx, ``bad_range``). Two shapes are not read: a whole string that
is a SHA-256 hex digest (``^[0-9a-f]{64}$``, the ``sha256`` / ``code_sha256`` fields), and a whole
string that is a canonical lower-case UUID (a version id cited in a code rule's
``derived_from.sources``, feature 018). Both are generated, not authored values, and their digit runs
read as SSNs by chance (``ssn_us@1`` reads a run between letters valid with ``letter_boundary``;
about one random UUID in twenty read valid, which refused public pushes at random).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from .compose import CompiledRuleSet
from .rule_set import RuleSetDocument, ValidatorSignal
from .validators import VALIDATORS, Validator, ValidatorDecl, resolve


def _bearing_from_scan_or_check(validator: Any, case: dict[str, Any]) -> list[ValidatorDecl]:
    params = case.get("params", {})
    inp = case["input"]
    if "text" in inp:
        valid = any(c.verdict.valid for c in validator.scan(inp["text"], params))
    else:
        valid = validator.check(
            inp["value"], inp.get("before", ""), inp.get("after", ""), params
        ).valid
    return [validator.decl] if valid and validator.decl.value_bearing else []


def _bearing_from_rule_set(compiled: CompiledRuleSet, case: dict[str, Any]) -> list[ValidatorDecl]:
    from .cases import case_input

    text, element = case_input(case)
    signals = compiled.signal_results(text, element)
    out: list[ValidatorDecl] = []
    for signal in compiled.document.signals:
        if not isinstance(signal, ValidatorSignal):
            continue
        decl = resolve(signal.validator.id, signal.validator.version).decl
        if decl.value_bearing and any(
            s.outputs.get("valid") is True for s in signals[signal.id].candidates
        ):
            out.append(decl)
    return out


def judge(case_id: str, source: dict[str, Any], bearing: list[ValidatorDecl]) -> str | None:
    """The violation for one case, or None."""
    if not bearing:
        return None
    kind = source.get("kind")
    refs = ", ".join(sorted({d.ref for d in bearing}))
    if kind == "published_test_value":
        if source.get("citation"):
            return None
        return f"{case_id}: published_test_value without a citation (valid for {refs})"
    if kind == "published_reference_value":
        if all(d.institution_identifier for d in bearing):
            return None
        return f"{case_id}: published_reference_value is only for institution identifiers ({refs})"
    if kind == "unissuable":
        if all(d.issuance_rule is not None for d in bearing):
            return None
        return f"{case_id}: claimed unissuable, but {refs} reads it valid and declares no issuance rule"
    return f"{case_id}: a value {refs} reads valid carries source {kind!r}, not a public source"


def contract_violations(cases_file: dict[str, Any]) -> list[str]:
    """Every violation in the contract file's validator and rule-kind sections."""
    out: list[str] = []
    for ref, section in cases_file["validators"].items():
        vid, version = ref.rsplit("@", 1)
        validator = resolve(vid, int(version))
        for case in section["cases"]:
            found = judge(case["id"], case["source"], _bearing_from_scan_or_check(validator, case))
            if found:
                out.append(found)
    compiled: dict[str, CompiledRuleSet] = {}
    for kind, cases in cases_file["rule_kinds"].items():
        for case in cases:
            if "document" not in case or not isinstance(case["document"], str):
                continue
            doc_id = case["document"]
            if doc_id not in compiled:
                compiled[doc_id] = CompiledRuleSet(
                    RuleSetDocument.model_validate(cases_file["rule_documents"][doc_id])
                )
            found = judge(
                f"{kind}/{case['id']}",
                case["source"],
                _bearing_from_rule_set(compiled[doc_id], case),
            )
            if found:
                out.append(found)
    return out


def document_violations(document: RuleSetDocument) -> list[str]:
    """Every violation among a rule set's embedded cases (pilot pack, fixtures, carried sets).
    A code-rule signal is admitted (feature 018): only validator candidates bear values, and a code
    rule has none, so its count is never needed here."""
    compiled = CompiledRuleSet(document, code_rules=True)
    out: list[str] = []
    for case in document.cases:
        data = case.model_dump(mode="json", exclude_none=True)
        found = judge(
            f"{document.name}/{case.id}", data["source"], _bearing_from_rule_set(compiled, data)
        )
        if found:
            out.append(found)
    return out


# --- every string (D38-2) -------------------------------------------------------------------------

_NOT_ALNUM = re.compile(r"[^0-9A-Za-z]")
_PUBLIC_INPUT_KEYS = ("value", "before", "after", "text")
#: A SHA-256 hex digest: a hash output, never read (see the module docstring).
_DIGEST = re.compile(r"[0-9a-f]{64}")
#: A canonical UUID (a version id in ``derived_from.sources``): generated, never read either.
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def identifier_readers() -> list[Validator]:
    """The format validators of the LIVE registry: value-bearing and declaring a masked shape."""
    return [
        v for v in VALIDATORS.values() if v.decl.value_bearing and v.decl.mask_shape is not None
    ]


def _value(text: str) -> str:
    return _NOT_ALNUM.sub("", text).upper()


def readings(text: str) -> Iterator[tuple[ValidatorDecl, str, str]]:
    """``(reader, substring, value)`` for every identifier reader that reads part of ``text``
    valid; ``value`` is the verdict's ``normalised`` form (else the substring), letters and digits
    only, upper-cased."""
    for reader in identifier_readers():
        for c in reader.scan(text, {}):
            if c.verdict.valid:
                sub = text[c.start : c.end]
                yield reader.decl, sub, _value(getattr(c.verdict, "normalised", None) or sub)


def _walk(value: Any, pointer: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield pointer, value
    elif isinstance(value, dict):
        for key, item in value.items():
            esc = str(key).replace("~", "~0").replace("/", "~1")
            yield pointer + "/" + esc, str(key)
            yield from _walk(item, pointer + "/" + esc)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from _walk(item, f"{pointer}/{i}")


def _cases(data: Any) -> Iterator[dict[str, Any]]:
    """Every object that carries a ``source`` and an ``input`` (a case), wherever it sits."""
    if isinstance(data, dict):
        if isinstance(data.get("source"), dict) and isinstance(data.get("input"), dict):
            yield data
        for item in data.values():
            yield from _cases(item)
    elif isinstance(data, list):
        for item in data:
            yield from _cases(item)


def documented_values(data: Any) -> tuple[set[str], set[str]]:
    """``(public for every reader, public for institution readers only)``, from the inputs of the
    cases with a public source."""
    every: set[str] = set()
    institution: set[str] = set()
    for case in _cases(data):
        source = case["source"]
        kind = source.get("kind")
        for key in _PUBLIC_INPUT_KEYS:
            text = case["input"].get(key)
            if not isinstance(text, str):
                continue
            for decl, _, value in readings(text):
                if kind == "published_test_value" and source.get("citation"):
                    every.add(value)
                elif kind == "published_reference_value" and decl.institution_identifier:
                    institution.add(value)
    return every, institution


def string_violations(data: Any) -> list[str]:
    """Every valid-looking, undocumented value in any string of a public file (D38-2)."""
    every, institution = documented_values(data)
    out: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for pointer, text in _walk(data):
        if _DIGEST.fullmatch(text) or _UUID.fullmatch(text):
            continue
        for decl, found, value in readings(text):
            if value in every or (decl.institution_identifier and value in institution):
                continue
            key = (pointer, decl.ref, found)
            if key not in seen:
                seen.add(key)
                out.append(
                    f"{pointer or '/'}: {found!r} reads valid as {decl.ref} and is not a "
                    "documented public value"
                )
    return out
