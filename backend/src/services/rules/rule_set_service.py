"""Rule sets: save, validate, preview, list, get, clone and difference (FR-016.16 – FR-016.21).

**Save is the only write** (FR-016.17). It runs the pipeline of FTID 016 section 7.1 and stops at the
first failing stage, naming the item and a ``next`` tool:

``schema -> NFC -> caps -> dialect scan of every pattern -> validators exist -> code_rule refused ->
(compile) -> minimum cases -> run every case -> provenance -> name ownership -> lock + number ->
insert``.

**Validate** runs the same stages and reports EVERY problem and every failing case (expected,
actual, each signal's verdict, the step that decided) without writing. **Preview** evaluates a
document on one text: the text is never stored or logged (FR-016.19); only its byte length and
counts are logged, and any exception inside evaluation becomes ``rule_signal_error`` BEFORE it can
reach the unexpected-error handler that logs a stack trace.

Concurrency (FTDD 016 section 8.1): one transaction inserts the head if absent, locks it, compares
the behaviour key, numbers ``max + 1`` and inserts; a unique violation that still slips through is
``409 rule_set_conflict``, never a 500. A save whose canonical bytes equal the latest version's
creates nothing and answers that version (``created: false``).

Identity is RFC 8785 over the document AS RECEIVED (``rules/rule_set.canonical_bytes``, D38-4); the
stored ``body_canonical`` is those bytes, which the database CHECKs against ``body_sha256``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ...core.agent_origin import Who
from ...core.config import get_settings
from ...core.ids import new_id
from ...models.publish import ConfigKind, ConfigVersion
from ...models.rule_set import RuleSet, RuleSetUse
from ...models.version import Version, VersionStep
from ...rules import cases as case_runner
from ...rules import dialect, rule_set
from ...rules.compose import CompiledRuleSet, RuleSetNotRunnable, RuleSignalError
from ...rules.difference import apply_pointer, difference
from ...rules.next_step import next_for
from ...rules.rule_set import (
    CodeRuleSignal,
    LexicalWeightsSignal,
    NormaliseValidator,
    PatternSignal,
    RuleSetDocument,
    ValidatorSignal,
)
from ...rules.validators import resolve as resolve_validator
from .errors import RuleSetError, problem

logger = logging.getLogger(__name__)

KIND = ConfigKind.RULE_SET.value
PLUGIN = "rule_set"

#: The environment name of each cap a save, validate or clone checks (FR-016.37).
CAPS = {
    "signals": "RULE_SET_MAX_SIGNALS",
    "cases": "RULE_SET_MAX_CASES",
    "terms": "RULE_SET_MAX_TERMS",
    "code_params": "RULE_SET_MAX_CODE_PARAMS_BYTES",
    "document": "RULE_SET_MAX_DOCUMENT_BYTES",
}


def cap_limits() -> dict[str, int]:
    """The caps in force now (runtime-changeable, "next request")."""
    settings = get_settings()
    return {
        "signals": settings.rule_set_max_signals,
        "cases": settings.rule_set_max_cases,
        "terms": settings.rule_set_max_terms,
        "code_params": settings.rule_set_max_code_params_bytes,
        "document": settings.rule_set_max_document_bytes,
    }


# --- the pipeline -------------------------------------------------------------------------------


@dataclass
class Checked:
    """What the pipeline knows about one document."""

    raw: dict[str, Any]
    document: RuleSetDocument | None = None
    compiled: CompiledRuleSet | None = None
    canonical: bytes = b""
    sha256: str = ""
    problems: list[RuleSetError] = field(default_factory=list)
    failing_cases: list[dict[str, Any]] = field(default_factory=list)
    cases_run: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems and not self.failing_cases


def _pointer_of(raw: Any, loc: Sequence[Any]) -> str:
    """The JSON Pointer of a pydantic error location, following only the keys the data has (a
    discriminated union adds its tag to ``loc``)."""
    node: Any = raw
    parts: list[str] = []
    for item in loc:
        if isinstance(node, dict) and item in node:
            node = node[item]
        elif isinstance(node, list) and isinstance(item, int) and 0 <= item < len(node):
            node = node[item]
        elif isinstance(node, dict) and node.get("kind") == item:
            continue  # the discriminator's tag, not a key
        else:
            parts.append(str(item).replace("~", "~0").replace("/", "~1"))
            break  # a missing member: name it and stop
        parts.append(str(item).replace("~", "~0").replace("/", "~1"))
    return "".join("/" + p for p in parts)


def _schema_error(raw: Any, exc: ValidationError) -> RuleSetError:
    first = exc.errors()[0]
    for error in exc.errors():
        if error["type"] == "not_nfc":
            pointer = error["ctx"]["pointer"]
            return RuleSetError(
                422,
                "not_nfc",
                f"The string at {pointer} is not Unicode NFC; normalise it (NFC) and save again. "
                "Nothing is normalised silently.",
                {"pointer": pointer},
                next_tool="dataworks_validate_rule_set",
                why="re-check the corrected document",
            )
        if error["type"] == "unsafe_integer":
            pointer = error["ctx"]["pointer"]
            return RuleSetError(
                422,
                "rule_set_invalid",
                f"The integer at {pointer} is outside +/-(2^53 - 1); the rule-set identity "
                "(RFC 8785) cannot write it exactly.",
                {"pointer": pointer, "rule": "unsafe_integer"},
                next_tool="dataworks_validate_rule_set",
                why="re-check the corrected document",
            )
    pointer = _pointer_of(raw, first["loc"])
    return RuleSetError(
        422,
        "rule_set_invalid",
        f"The document is not a midataworks.rule-set/v1 document: at {pointer or '/'}: "
        f"{first['msg']}.",
        {"pointer": pointer, "message": first["msg"], "errors": len(exc.errors())},
        next_tool="dataworks_validate_rule_set",
        why="re-check the corrected document",
    )


def _cap(
    limits: Mapping[str, int], what: str, actual: int, item: dict[str, Any]
) -> RuleSetError | None:
    env, limit = CAPS[what], int(limits[what])
    if actual <= limit:
        return None
    return RuleSetError(
        413,
        "cap_exceeded",
        f"{env} allows {limit}; this has {actual}. Nothing was truncated.",
        {"setting": env, "limit": limit, "actual": actual, **item},
    )


def _caps(
    document: RuleSetDocument, canonical: bytes, limits: Mapping[str, int]
) -> list[RuleSetError]:
    found = [
        _cap(limits, "document", len(canonical), {"item": "document bytes"}),
        _cap(limits, "signals", len(document.signals), {"item": "signals"}),
        _cap(limits, "cases", len(document.cases), {"item": "cases"}),
    ]
    for signal in document.signals:
        if isinstance(signal, LexicalWeightsSignal):
            found.append(_cap(limits, "terms", len(signal.terms), {"signal": signal.id}))
        if isinstance(signal, CodeRuleSignal):
            size = len(rule_set.canonical_bytes(signal.params))
            found.append(_cap(limits, "code_params", size, {"signal": signal.id}))
    return [f for f in found if f is not None]


def _patterns(document: RuleSetDocument) -> list[RuleSetError]:
    out: list[RuleSetError] = []
    for index, signal in enumerate(document.signals):
        if not isinstance(signal, PatternSignal):
            continue
        try:
            dialect.compile_pattern(signal.pattern, signal.ignore_case)
        except dialect.PatternRefused as exc:
            out.append(
                RuleSetError(
                    422,
                    "pattern_refused",
                    f"Signal {signal.id!r}: {exc}",
                    {
                        "signal": signal.id,
                        "pointer": f"/signals/{index}/pattern",
                        "rule": exc.rule,
                        "position": exc.position,
                    },
                    next_tool="dataworks_howto",
                    next_args={"topic": "rule_signals"},
                    why="read the pattern dialect",
                )
            )
    return out


def _validator_refs(document: RuleSetDocument) -> list[RuleSetError]:
    refs: list[tuple[str, str, int]] = [
        (f"signal {s.id!r}", s.validator.id, s.validator.version)
        for s in document.signals
        if isinstance(s, ValidatorSignal)
    ]
    refs += [
        (f"rendering {r.rendering!r}", r.normaliser.validator.id, r.normaliser.validator.version)
        for r in document.renderings
        if isinstance(r.normaliser, NormaliseValidator)
    ]
    out: list[RuleSetError] = []
    for where, vid, version in refs:
        try:
            resolve_validator(vid, version)
        except LookupError:
            out.append(
                RuleSetError(
                    422,
                    "validator_unknown",
                    f"{where} names validator {vid}@{version}, which is not registered.",
                    {"item": where, "validator": vid, "version": version},
                    next_tool="dataworks_list_validators",
                    why="see the registered validator versions",
                )
            )
    return out


def _code_rules(document: RuleSetDocument) -> list[RuleSetError]:
    return [
        RuleSetError(
            422,
            "code_rule_not_available",
            f"Signal {s.id!r} is a code_rule; code rules arrive with feature 018.",
            {"signal": s.id},
            next_tool="dataworks_howto",
            next_args={"topic": "rule_signals"},
            why="see the signal kinds available now",
        )
        for s in document.signals
        if isinstance(s, CodeRuleSignal)
    ]


def case_report(compiled: CompiledRuleSet, case: Any) -> dict[str, Any]:
    """One case's full report: expected, actual, every signal's verdict, the deciding step."""
    data = case.model_dump(mode="json", exclude_none=True)
    text, element = case_runner.case_input(data)
    outcome = case_runner.run_rule_case(compiled, case)
    result = compiled.evaluate(text, element)
    return {
        "case": case.id,
        "passed": outcome.passed,
        "mismatches": outcome.mismatches,
        "expected": data["expect"],
        "actual": {"outputs": result.outputs, "vetoed": result.vetoed},
        "signals": result.signals,
        "deciding": result.deciding,
    }


def check(
    raw: Any,
    limits: Mapping[str, int] | None = None,
    *,
    collect: bool = False,
    cases: bool = True,
) -> Checked:
    """Run the pipeline's pure stages. ``collect=False`` stops at the first problem (save);
    ``collect=True`` gathers every problem and every failing case (validate, clone).
    ``cases=False`` stops after the document compiles (preview: a draft need not pass its cases
    to be tried on a text)."""
    limits = cap_limits() if limits is None else limits
    if not isinstance(raw, dict):
        raise RuleSetError(422, "rule_set_invalid", "The document must be a JSON object.", {})
    checked = Checked(raw=raw)
    try:
        document = rule_set.parse(raw)
    except ValidationError as exc:
        checked.problems.append(_schema_error(raw, exc))
        return checked
    checked.document = document
    checked.canonical = rule_set.canonical_bytes(raw)
    checked.sha256 = rule_set.digest(raw)
    stages: list[Callable[[], list[RuleSetError]]] = [
        lambda: _caps(document, checked.canonical, limits),
        lambda: _patterns(document),
        lambda: _validator_refs(document),
        lambda: _code_rules(document),
    ]
    for stage in stages:
        found = stage()
        checked.problems += found
        if found and not collect:
            return checked
    if checked.problems:
        return checked
    try:
        compiled = CompiledRuleSet(document)
    except RuleSetNotRunnable as exc:
        checked.problems.append(
            RuleSetError(
                422,
                exc.code,
                str(exc),
                dict(exc.details),
                next_tool="dataworks_validate_rule_set",
                why="re-check the corrected document",
            )
        )
        return checked
    checked.compiled = compiled
    if not cases:
        return checked
    try:
        _run_cases(checked, compiled, document, collect)
    except RuleSignalError as exc:
        logger.warning("rule-set case run: signal %s raised %s", exc.signal_id, exc.error_type)
        checked.problems.append(
            RuleSetError(
                422,
                "rule_signal_error",
                f"Signal {exc.signal_id!r} ({exc.validator}) raised {exc.error_type} while the "
                "cases ran; no verdict.",
                {"signal": exc.signal_id, "validator": exc.validator},
                next_tool="dataworks_get_validator",
                next_args={
                    "validator_id": exc.validator.split("@")[0],
                    "version": int(exc.validator.split("@")[1]),
                },
                why="read the validator the signal names",
            )
        )
    return checked


def _run_cases(
    checked: Checked, compiled: CompiledRuleSet, document: RuleSetDocument, collect: bool
) -> None:
    missing = case_runner.minimum_cases(compiled)
    if missing:
        checked.problems.append(
            RuleSetError(
                422,
                "cases_missing",
                "The rule set lacks the cases every saved rule set carries: "
                + "; ".join(f"{m.get('output') or m.get('veto')}: {m['needs']}" for m in missing),
                {"missing": missing},
                next_tool="dataworks_validate_rule_set",
                why="add the missing cases and re-check",
            )
        )
        if not collect:
            return
    for case in document.cases:
        report = case_report(compiled, case)
        checked.cases_run += 1
        if not report["passed"]:
            checked.failing_cases.append(report)
            if not collect:
                break


def _first_refusal(checked: Checked) -> RuleSetError | None:
    if checked.problems:
        return checked.problems[0]
    if checked.failing_cases:
        failing = checked.failing_cases[0]
        return RuleSetError(
            422,
            "case_failed",
            f"Case {failing['case']!r} fails: {'; '.join(failing['mismatches'])}.",
            {
                "case": failing["case"],
                "expected": failing["expected"],
                "actual": failing["actual"],
                "signals": failing["signals"],
                "deciding": failing["deciding"],
            },
            next_tool="dataworks_validate_rule_set",
            why="see every failing case with each signal's verdict",
        )
    return None


# --- reading stored versions --------------------------------------------------------------------


def _version_row(session: Session, name: str, version: int | None) -> ConfigVersion:
    query = select(ConfigVersion).where(ConfigVersion.kind == KIND, ConfigVersion.name == name)
    if version is None:
        query = query.order_by(ConfigVersion.number.desc()).limit(1)
    else:
        query = query.where(ConfigVersion.number == version)
    row = session.execute(query).scalar_one_or_none()
    if row is None:
        label = name if version is None else f"{name}@{version}"
        raise RuleSetError(
            404,
            "rule_set_not_found",
            f"No stored rule set {label}.",
            {"name": name, "version": version},
            next_tool="dataworks_list_rule_sets",
            why="find a stored rule set",
        )
    return row


def stored_document(row: ConfigVersion) -> dict[str, Any]:
    """The stored document AS SAVED (parsed from the canonical text, not the JSONB copy)."""
    assert row.body_canonical is not None
    parsed: dict[str, Any] = json.loads(row.body_canonical)
    return parsed


def _document_of(
    session: Session, document: dict[str, Any] | None, name: str | None, version: int | None
) -> dict[str, Any]:
    if (document is None) == (name is None):
        raise RuleSetError(
            422,
            "rule_set_invalid",
            "Give either a document, or a stored rule set's name (and optionally version).",
            {},
        )
    if document is not None:
        return document
    assert name is not None
    return stored_document(_version_row(session, name, version))


# --- save ---------------------------------------------------------------------------------------

ProvenanceCheck = Callable[[Session, RuleSetDocument], None]


def _derivation_provenance(session: Session, document: RuleSetDocument) -> None:
    from .derivation_service import check_provenance

    check_provenance(session, document)


#: Slice 4: the derivation row decides (``derivation_service.check_provenance``).
PROVENANCE_CHECK: list[ProvenanceCheck] = [_derivation_provenance]


def _insert(session: Session, checked: Checked, who: Who) -> tuple[ConfigVersion, bool]:
    document = checked.document
    assert document is not None
    session.execute(
        pg_insert(RuleSet)
        .values(
            name=document.name,
            behaviour_key=document.behaviour_key,
            created_by=who.who,
            created_by_origin=who.origin,
        )
        .on_conflict_do_nothing(index_elements=["name"])
    )
    head = session.execute(
        select(RuleSet).where(RuleSet.name == document.name).with_for_update()
    ).scalar_one()
    if head.behaviour_key != document.behaviour_key:
        raise RuleSetError(
            409,
            "rule_set_name_taken",
            f"The name {document.name} belongs to behaviour key {head.behaviour_key}; a name has "
            "one key. Choose another name, or clone it to the new key.",
            {"name": document.name, "behaviour_key": head.behaviour_key},
            next_tool="dataworks_clone_rule_set",
            next_args={"name": document.name, "new_behaviour_key": document.behaviour_key},
            why="copy the rule set to the other behaviour under a new name",
        )
    latest = session.execute(
        select(ConfigVersion)
        .where(ConfigVersion.kind == KIND, ConfigVersion.name == document.name)
        .order_by(ConfigVersion.number.desc())
        .limit(1)
    ).scalar_one_or_none()
    if latest is not None and latest.body_sha256 == checked.sha256:
        return latest, False
    number = (latest.number if latest is not None else 0) + 1
    row = ConfigVersion(
        id=new_id("rsv"),
        kind=KIND,
        name=document.name,
        number=number,
        plugin=PLUGIN,
        body=checked.raw,
        body_sha256=checked.sha256,
        body_canonical=checked.canonical.decode("utf-8"),
        parent_id=latest.id if latest is not None else None,
        created_by=who.who,
        created_by_origin=who.origin,
    )
    session.add(row)
    session.flush()
    return row, True


def saved_out(row: ConfigVersion, created: bool) -> dict[str, Any]:
    return {
        "name": row.name,
        "version": row.number,
        "sha256": row.body_sha256,
        "behaviour_key": row.body["behaviour_key"],
        "created": created,
        "next": next_for(
            "dataworks_get_rule_set",
            {"name": row.name, "version": row.number},
            "read the stored version, its cases and the builds that used it",
        ),
    }


def save(session: Session, raw: Any, who: Who) -> dict[str, Any]:
    """The only write (FR-016.17): every stage, then the locked insert. Commits."""
    checked = check(raw)
    refusal = _first_refusal(checked)
    if refusal is not None:
        raise refusal
    return _commit_save(session, checked, who)


def _commit_save(session: Session, checked: Checked, who: Who) -> dict[str, Any]:
    assert checked.document is not None
    for provenance_check in PROVENANCE_CHECK:
        provenance_check(session, checked.document)
    try:
        row, created = _insert(session, checked, who)
        session.commit()
    except IntegrityError:
        session.rollback()
        raise RuleSetError(
            409,
            "rule_set_conflict",
            f"Another save of {checked.document.name} took this version number first; read the "
            "latest version and save again.",
            {"name": checked.document.name},
            next_tool="dataworks_get_rule_set",
            next_args={"name": checked.document.name},
            why="read the version saved concurrently",
        ) from None
    except BaseException:
        session.rollback()
        raise
    logger.info(
        "rule set %s@%d %s by %s (%s)",
        row.name,
        row.number,
        "saved" if created else "unchanged",
        who.who,
        who.origin,
    )
    return saved_out(row, created)


# --- validate and preview -----------------------------------------------------------------------


def validate(
    session: Session,
    document: dict[str, Any] | None = None,
    name: str | None = None,
    version: int | None = None,
) -> dict[str, Any]:
    """Every problem and every failing case; writes nothing (FR-016.18)."""
    raw = _document_of(session, document, name, version)
    checked = check(raw, collect=True)
    valid = checked.ok
    return {
        "valid": valid,
        "sha256": checked.sha256 or None,
        "problems": [problem(p) for p in checked.problems],
        "failing_cases": checked.failing_cases,
        "cases_run": checked.cases_run,
        "next": (
            next_for("dataworks_save_rule_set", {}, "save the document (it passes every stage)")
            if valid
            else next_for("dataworks_validate_rule_set", {}, "fix the listed items, then re-check")
        ),
    }


def preview(
    session: Session,
    text: str,
    document: dict[str, Any] | None = None,
    name: str | None = None,
    version: int | None = None,
    element: str | None = None,
) -> dict[str, Any]:
    """One text through one document (FR-016.19). The text is never stored or logged."""
    size = len(text.encode("utf-8")) + len((element or "").encode("utf-8"))
    limit = get_settings().rule_preview_max_text_bytes
    if size > limit:
        raise RuleSetError(
            413,
            "preview_text_too_large",
            f"The text is {size} bytes; RULE_PREVIEW_MAX_TEXT_BYTES allows {limit}.",
            {"setting": "RULE_PREVIEW_MAX_TEXT_BYTES", "limit": limit, "actual": size},
        )
    raw = _document_of(session, document, name, version)
    checked = check(raw, cases=False)
    if checked.compiled is None:
        refusal = _first_refusal(checked)
        assert refusal is not None
        raise refusal
    try:
        result = checked.compiled.evaluate(text, element)
    except RuleSignalError as exc:
        logger.warning("rule-set preview: signal %s raised %s", exc.signal_id, exc.error_type)
        raise RuleSetError(
            422,
            "rule_signal_error",
            f"Signal {exc.signal_id!r} ({exc.validator}) raised {exc.error_type}; no verdict.",
            {"signal": exc.signal_id, "validator": exc.validator},
        ) from None
    except Exception as exc:  # noqa: BLE001 - never reaches the stack-trace logger with text
        logger.warning("rule-set preview: evaluation raised %s", type(exc).__name__)
        raise RuleSetError(
            422,
            "rule_signal_error",
            f"Evaluation raised {type(exc).__name__}; no verdict.",
            {"signal": None},
        ) from None
    logger.info(
        "rule-set preview: %d bytes, %d of %d signals fired",
        size,
        sum(1 for s in result.signals.values() if s["fired"]),
        len(result.signals),
    )
    return {
        "sha256": checked.sha256,
        "spans_unit": "code_points",
        "signals": result.signals,
        "expressions": result.expressions,
        "vetoed": result.vetoed,
        "vetoes": result.vetoes,
        "outputs": result.outputs,
        "deciding": result.deciding,
        "next": next_for(
            "dataworks_validate_rule_set", {}, "run every embedded case of the document"
        ),
    }


# --- list and get -------------------------------------------------------------------------------


def _counts(body: Mapping[str, Any]) -> dict[str, int]:
    return {k: len(body.get(k) or []) for k in ("signals", "outputs", "vetoes", "cases")}


def _last_validation(row: ConfigVersion) -> dict[str, Any]:
    """Every stored version passed every case at save (the save is a validation)."""
    return {
        "source": "save",
        "at": row.created_at.isoformat() if row.created_at else None,
        "passed": True,
        "cases": len(row.body.get("cases") or []),
    }


def list_rule_sets(
    session: Session,
    *,
    page: int,
    limit: int,
    behaviour_key: str | None = None,
    include_archived: bool = False,
) -> dict[str, Any]:
    """One row per name with its latest version, by name, paged (FR-016.20, FR-016.39)."""
    heads = select(RuleSet)
    if behaviour_key is not None:
        heads = heads.where(RuleSet.behaviour_key == behaviour_key)
    if not include_archived:
        heads = heads.where(RuleSet.archived_at.is_(None))
    total = session.execute(select(func.count()).select_from(heads.subquery())).scalar_one()
    rows = list(
        session.execute(heads.order_by(RuleSet.name).offset((page - 1) * limit).limit(limit))
        .scalars()
        .all()
    )
    latest = {
        r.name: r
        for r in session.execute(
            select(ConfigVersion)
            .where(ConfigVersion.kind == KIND, ConfigVersion.name.in_([h.name for h in rows]))
            .order_by(ConfigVersion.name, ConfigVersion.number)
        ).scalars()
    }
    items = []
    for head in rows:
        version = latest.get(head.name)
        items.append(
            {
                "name": head.name,
                "behaviour_key": head.behaviour_key,
                "version": version.number if version else None,
                "sha256": version.body_sha256 if version else None,
                "counts": _counts(version.body) if version else {},
                "last_validation": _last_validation(version) if version else None,
                "archived": head.archived_at is not None,
            }
        )
    return {"items": items, "page": page, "limit": limit, "total": int(total)}


def used_by(session: Session, config_version_id: str) -> list[dict[str, Any]]:
    """Every version whose build ran this rule-set version (FR-016.20)."""
    rows = session.execute(
        select(
            Version.id, Version.dataset_id, Version.number, VersionStep.step_index, RuleSetUse.use
        )
        .join(VersionStep, VersionStep.step_execution_id == RuleSetUse.step_execution_id)
        .join(Version, Version.id == VersionStep.version_id)
        .where(RuleSetUse.config_version_id == config_version_id)
        .order_by(Version.created_at, VersionStep.step_index)
    ).all()
    return [
        {
            "version_id": str(r[0]),
            "dataset_id": str(r[1]),
            "number": r[2],
            "step_index": r[3],
            "use": r[4],
        }
        for r in rows
    ]


def get(session: Session, name: str, version: int | None = None) -> dict[str, Any]:
    row = _version_row(session, name, version)
    head = session.get(RuleSet, name)
    later = session.execute(
        select(func.min(ConfigVersion.number)).where(
            ConfigVersion.kind == KIND,
            ConfigVersion.name == name,
            ConfigVersion.number > row.number,
        )
    ).scalar_one_or_none()
    document = stored_document(row)
    return {
        "name": row.name,
        "version": row.number,
        "behaviour_key": document["behaviour_key"],
        "sha256": row.body_sha256,
        "document": document,
        "cases": document.get("cases", []),
        "last_validation": _last_validation(row),
        "superseded_by": later,
        "archived": head is not None and head.archived_at is not None,
        "created_by": row.created_by,
        "created_by_origin": row.created_by_origin,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "used_by": used_by(session, row.id),
        "next": next_for(
            "dataworks_preview_rule_set",
            {"name": row.name, "version": row.number},
            "try the rule set on a text",
        ),
    }


# --- clone and difference -----------------------------------------------------------------------


def clone(
    session: Session,
    who: Who,
    *,
    name: str,
    version: int | None,
    new_name: str,
    new_behaviour_key: str,
    substitutions: Sequence[Mapping[str, Any]],
    replacement_cases: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Copy a version to another name and behaviour key, with substitutions by JSON Pointer; the
    copy must pass its cases before it is saved (FR-016.21). Nothing is saved on a failure."""
    source = _version_row(session, name, version)
    document = stored_document(source)
    for sub in substitutions:
        pointer = str(sub["pointer"])
        if pointer in ("/name", "/behaviour_key"):
            raise RuleSetError(
                422,
                "rule_set_invalid",
                f"{pointer} is set by new_name and new_behaviour_key, not by a substitution.",
                {"pointer": pointer},
            )
        if pointer == "/provenance" or pointer.startswith("/provenance/"):
            # Review R1 (slices 2-4): a copy of a derived rule set keeps where its rules were
            # learned; dropping it here would lift the source's visibility floor in one call.
            raise RuleSetError(
                422,
                "rule_set_invalid",
                f"{pointer}: a clone keeps the source's provenance; it is not substituted.",
                {"pointer": pointer},
                next_tool="dataworks_clone_rule_set",
                next_args={"name": name, "new_name": new_name},
                why="clone again with substitutions on signals and cases only",
            )
        try:
            document = apply_pointer(document, pointer, sub["value"])
        except (KeyError, IndexError, ValueError, TypeError):
            raise RuleSetError(
                422,
                "rule_set_invalid",
                f"The substitution pointer {pointer} names nothing in {name}@{source.number}.",
                {"pointer": pointer},
            ) from None
    document["name"] = new_name
    document["behaviour_key"] = new_behaviour_key
    if replacement_cases:
        by_id = {c.get("id"): n for n, c in enumerate(document.get("cases") or [])}
        for case in replacement_cases:
            if case.get("id") not in by_id:
                raise RuleSetError(
                    422,
                    "rule_set_invalid",
                    f"replacement_cases names case {case.get('id')!r}, which {name}@"
                    f"{source.number} does not have; a replacement replaces a case of the same id.",
                    {"case": case.get("id")},
                )
            document["cases"][by_id[case["id"]]] = dict(case)
    checked = check(document, collect=True)
    if checked.problems:
        raise checked.problems[0]
    if checked.failing_cases:
        args = {
            "name": name,
            "version": source.number,
            "new_name": new_name,
            "new_behaviour_key": new_behaviour_key,
            "substitutions": [dict(s) for s in substitutions],
            "replacement_cases": [],
        }
        raise RuleSetError(
            422,
            "clone_cases_failed",
            f"The clone fails {len(checked.failing_cases)} case(s); supply replacement cases for "
            "them. Nothing was saved.",
            {
                "failing_cases": checked.failing_cases,
                "replace_case_ids": [f["case"] for f in checked.failing_cases],
            },
            next_tool="dataworks_clone_rule_set",
            next_args=args,
            why="call again with replacement_cases: one case per id in replace_case_ids that the "
            "substituted document passes",
        )
    out = _commit_save(session, checked, who)
    out["cloned_from"] = {"name": name, "version": source.number, "sha256": source.body_sha256}
    out["difference"] = difference(stored_document(source), document)
    return out


def parse_ref(ref: str) -> tuple[str, int]:
    """``name@version`` -> ``(name, version)``, refused naming the reference."""
    name, sep, number = ref.rpartition("@")
    if not sep or not name or not number.isdigit() or int(number) < 1:
        raise RuleSetError(
            422,
            "rule_set_invalid",
            f"{ref!r} is not a rule-set reference; write <name>@<version>, e.g. my-rules@2.",
            {"ref": ref},
        )
    return name, int(number)


def difference_of(session: Session, a: str, b: str) -> dict[str, Any]:
    (an, av), (bn, bv) = parse_ref(a), parse_ref(b)
    ra, rb = _version_row(session, an, av), _version_row(session, bn, bv)
    return {
        "a": {"name": an, "version": av, "sha256": ra.body_sha256},
        "b": {"name": bn, "version": bv, "sha256": rb.body_sha256},
        "difference": difference(stored_document(ra), stored_document(rb)),
    }


# --- recipe moves (FR-016.23) -------------------------------------------------------------------


async def planned_moves(
    db: AsyncSession, steps: Sequence[Mapping[str, Any]]
) -> dict[int, dict[str, Any]]:
    """Per ``rule_set@1`` step (1-based index): the pinned version, the latest stored version of
    the same name and the difference between them. Nothing changes until the recipe is saved."""
    from .step_resolution import OPERATOR, pin_of

    out: dict[int, dict[str, Any]] = {}
    for index, step in enumerate(steps, start=1):
        if step.get("operator") != OPERATOR:
            continue
        pin = pin_of(step.get("params") or {})
        if pin is None:
            continue
        name, version, sha = pin
        rows = (
            (
                await db.execute(
                    select(ConfigVersion)
                    .where(ConfigVersion.kind == KIND, ConfigVersion.name == name)
                    .order_by(ConfigVersion.number)
                )
            )
            .scalars()
            .all()
        )
        pinned = next((r for r in rows if r.number == version), None)
        latest = rows[-1] if rows else None
        head = await db.get(RuleSet, name)
        moves = latest is not None and pinned is not None and latest.number != pinned.number
        out[index] = {
            "from": {"name": name, "version": version, "sha256": sha},
            "to": (
                {"name": name, "version": latest.number, "sha256": latest.body_sha256}
                if moves and latest is not None
                else None
            ),
            "archived": head is not None and head.archived_at is not None,
            "difference": (
                difference(stored_document(pinned), stored_document(latest))
                if moves and pinned is not None and latest is not None
                else []
            ),
        }
    return out
