"""How sensitive a version's data is: the floor at build time, the effective value now, and what
lowering it takes (021 FR-021.17 - FR-021.24; FTDD §6.3; FTID I8; suite P41, P42).

Values, in order: ``not_sensitive`` < not declared (``None``) < ``sensitive``. The order is a
dictionary, never string comparison. Not declared is UNKNOWN, which miWorkbench and miForge treat
as sensitive (suite P11).

- :func:`floor` is the value finalize stores: the strictest of the recipe's declaration and the
  effective value of every version input, rule-source ancestor and build binding's version, plus
  ``sensitive`` for a session-data source read directly. A declaration of ``not_sensitive`` is
  refused ``sensitivity_below_floor`` over a ``sensitive`` input or a session-data source
  (FR-021.19); otherwise it LOWERS, which an agent may do only with the operator's approval
  (:func:`lowers`, ``sensitivity_declare``, P42).
- :func:`effective` is the value every surface reads: the stored floor, raised (never lowered) to
  ``sensitive`` when the version's lineage reaches a source flagged as session data, whenever that
  flag was set (P42). **This module is the only reader of** ``Version.sensitivity`` (FR-021.20);
  the proof is behavioural (``test_sensitivity_every_surface``), not a source scan.
- :func:`implied_floor` is the visibility floor a check reads: ``private_only`` for a sensitive
  version (P42), else the stored floor.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

ORDER: dict[str | None, int] = {"not_sensitive": 0, None: 1, "sensitive": 2}
#: The registered build-binding kinds and the version columns each run records (spike 0.8, R3B-21).
#: A kind registered later must be added here; ``test_binding_kinds_are_listed`` fails until it is.
BINDING_VERSION_COLUMNS: dict[str, tuple[str, ...]] = {
    "label_run": ("input_version_id",),
    "generation_run": ("input_version_id", "held_out_origin_version_id"),
}


def strictest(values: Sequence[str | None]) -> str | None:
    return max(values, key=ORDER.__getitem__, default=None)


def _session_sources(session: Session, source_ids: Sequence[str]) -> list[dict[str, Any]]:
    from ..models.source import Source

    found: list[dict[str, Any]] = []
    for sid in source_ids:
        row = session.get(Source, sid)
        if row is not None and row.session_data:
            found.append({"kind": "session_data_source", "source_id": str(row.id)})
    return found


def _binding_versions(session: Session, version: Any) -> list[tuple[str, str]]:
    """``[(version_id, via)]`` of the runs the version's build bound. A run with no row is skipped
    here: a read can only raise, and a missing row raises nothing it could know of."""
    from ..models.generation import GenerationRun
    from ..models.label_run import LabelRun

    models = {"label_run": LabelRun, "generation_run": GenerationRun}
    out: list[tuple[str, str]] = []
    for binding in version.bindings or []:
        kind = str(binding.get("kind"))
        model = models.get(kind)
        run = session.get(model, binding.get("id")) if model is not None else None
        if run is None:
            continue
        for column in BINDING_VERSION_COLUMNS[kind]:
            out.append((str(getattr(run, column)), f"{kind}:{binding['id']}"))
    return out


def _lineage_edges(session: Session, version: Any) -> list[tuple[str, str]]:
    """``[(version_id, via)]``: every version this version's build READ, one level up: its
    version inputs and rule-source ancestors (``dw_version_inputs``, plus ``Version.inputs`` for a
    row written without the relational mirror) and the versions its bindings' runs read
    (R2 review fix (OR2-021-2), 2026-11-16)."""
    from ..models.enums import InputKind
    from ..models.version import VersionInput

    out: list[tuple[str, str]] = []
    rows = session.execute(
        select(VersionInput.kind, VersionInput.input_version_id)
        .where(
            VersionInput.version_id == version.id,
            VersionInput.kind.in_([InputKind.VERSION.value, InputKind.RULE_SOURCE.value]),
        )
        .order_by(VersionInput.position)
    ).all()
    for kind, input_version_id in rows:
        out.append((str(input_version_id), f"{kind}:{input_version_id}"))
    for item in version.inputs or []:
        if item.get("kind") in (InputKind.VERSION, InputKind.RULE_SOURCE) and item.get(
            "version_id"
        ):
            out.append((str(item["version_id"]), f"{item['kind']}:{item['version_id']}"))
    out.extend(_binding_versions(session, version))
    return out


def effective(session: Session, version: Any) -> tuple[str | None, dict[str, Any]]:
    """``(value, basis)``: the stored floor, raised to ``sensitive`` when any source in the
    version's lineage is session data now (P42). Only ever raises. ``basis`` is the stored basis
    with ``raised_by`` filled from the live lineage: one entry per flagged source, the version's
    own manifest sources first (no ``via``), then each ancestor's, naming in ``via`` the edge it
    was reached through.

    The lineage is the CLOSURE (R2 review fix (OR2-021-2), 2026-11-16): every version input,
    rule-source ancestor and binding's version, and theirs in turn. A manifest's ``sources``
    cover its inputs' sources but not their bindings (OR1-021-9 added the version's own bindings
    only), so a child of a bound version was missed. The walk is iterative, breadth first, with a
    visited set: a cycle ends and a deep chain cannot exhaust the stack (OR2-021-6, a
    ``RecursionError`` at depth 1100)."""
    from collections import deque

    from ..models.version import Version
    from .publishing.check_inputs import source_ids

    stored: str | None = version.sensitivity
    basis: dict[str, Any] = dict(version.sensitivity_basis or {"floor": stored, "declared": None})
    raised: list[dict[str, Any]] = []
    flagged: set[str] = set()
    seen: set[str] = {str(version.id)}
    queue: deque[tuple[Any, str | None]] = deque([(version, None)])
    while queue:
        row, via = queue.popleft()
        for found in _session_sources(session, source_ids(row)):
            if found["source_id"] in flagged:
                continue
            flagged.add(found["source_id"])
            raised.append(found if via is None else {**found, "via": via})
        for parent_id, edge in _lineage_edges(session, row):
            if parent_id in seen:
                continue
            seen.add(parent_id)
            parent = session.get(Version, parent_id)
            if parent is not None:
                queue.append((parent, edge))
    value = "sensitive" if raised else stored
    return value, {**basis, "floor": stored, "raised_by": raised}


#: "Not read yet": a caller that already holds :func:`effective`'s value passes it as ``now`` so the
#: lineage is walked once per read (R3 review fix (OR3-021-5), 2026-11-16).
UNREAD: Any = object()


def implied_floor(session: Session, version: Any, *, now: str | None = UNREAD) -> str | None:
    """The visibility floor checks read: ``private_only`` when the version is sensitive (P42), else
    the stored ``visibility_floor``. The stored column is never changed. ``now`` is
    :func:`effective`'s value when the caller already read it."""
    if now is UNREAD:
        now = effective(session, version)[0]
    if now == "sensitive":
        return "private_only"
    floor: str | None = version.visibility_floor
    return floor


def _contributing_versions(
    session: Session,
    items: Sequence[Mapping[str, Any]],
    bindings: Sequence[Mapping[str, Any]],
) -> list[tuple[str, str]]:
    """``[(version_id, via)]``: every version whose sensitivity a build inherits. A bound run with
    no row is refused (OVN-wsp-dataworks-6)."""
    from ..models.enums import InputKind
    from ..models.generation import GenerationRun
    from ..models.label_run import LabelRun

    out: list[tuple[str, str]] = []
    for item in items:
        if item.get("kind") in (InputKind.VERSION, InputKind.RULE_SOURCE) and item.get(
            "version_id"
        ):
            out.append((str(item["version_id"]), str(item["kind"])))
    models = {"label_run": LabelRun, "generation_run": GenerationRun}
    for binding in bindings:
        kind = str(binding["kind"])
        if kind not in BINDING_VERSION_COLUMNS:
            raise LookupError(
                f"binding kind {kind!r} has no sensitivity rule; add it to "
                "services/sensitivity.BINDING_VERSION_COLUMNS"
            )
        run = session.get(models[kind], binding["id"])
        if run is None:
            # OVN-wsp-dataworks-6 (R1 review fix, OR1-021-3): the resolver admitted the run when the
            # build was requested; a run with no row now (a race) has versions nobody can read, and
            # "not declared" would let a sensitive binding version publish publicly. Refuse.
            from .assembly import BuildRefusal

            raise BuildRefusal(
                f"Bound {kind} {binding['id']} no longer exists, so the sensitivity of the data it "
                "read cannot be known. Build again with runs that exist.",
                code="binding_not_found",
                details={"kind": kind, "id": str(binding["id"])},
            )
        for column in BINDING_VERSION_COLUMNS[kind]:
            out.append((str(getattr(run, column)), f"{kind}:{column}"))
    return out


def contributions(
    session: Session,
    items: Sequence[Mapping[str, Any]],
    bindings: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Every input's effective sensitivity: versions (inputs, rule-source ancestors, binding
    versions) as ``effective`` reads them, and each direct source (``None``, or ``sensitive`` when
    it is session data)."""
    from ..models.enums import InputKind
    from ..models.version import Version

    out: list[dict[str, Any]] = []
    for version_id, via in _contributing_versions(session, items, bindings):
        row = session.get(Version, version_id)
        if row is None:
            raise LookupError(f"version {version_id} does not exist")
        out.append(
            {
                "kind": "version",
                "version_id": version_id,
                "via": via,
                "sensitivity": effective(session, row)[0],
            }
        )
    direct = [str(i["source_id"]) for i in items if i.get("kind") == InputKind.SOURCE]
    flagged = {s["source_id"] for s in _session_sources(session, direct)}
    for sid in direct:
        out.append(
            {
                "kind": "source",
                "source_id": sid,
                "sensitivity": "sensitive" if sid in flagged else None,
            }
        )
    return out


def floor(
    session: Session,
    recipe_body: Mapping[str, Any],
    items: Sequence[Mapping[str, Any]],
    bindings: Sequence[Mapping[str, Any]] = (),
    *,
    recipe_revision_id: str | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """``(value, basis)`` for finalize (FR-021.18, FR-021.19). Raises BuildRefusal
    ``sensitivity_below_floor`` when ``not_sensitive`` is declared over a sensitive input."""
    from .assembly import BuildRefusal

    found = contributions(session, items, bindings)
    worst = strictest([c["sensitivity"] for c in found])
    declared = recipe_body.get("sensitivity")
    if declared == "sensitive":
        value: str | None = "sensitive"
    elif declared == "not_sensitive":
        above = [c for c in found if c["sensitivity"] == "sensitive"]
        if above:
            raise BuildRefusal(
                f"The recipe declares the data not sensitive, but {len(above)} input(s) are "
                "sensitive (a sensitive version, or a source of recorded user traffic). A "
                "version is never less sensitive than its inputs; remove the declaration or "
                "the inputs.",
                code="sensitivity_below_floor",
                details={"inputs": above},
            )
        value = "not_sensitive"
    else:
        value = worst
    return value, {
        "floor": value,
        "declared": declared,
        "recipe_revision_id": recipe_revision_id,
        "inputs": found,
        "raised_by": [],
    }


def lowers(
    session: Session,
    recipe_body: Mapping[str, Any],
    items: Sequence[Mapping[str, Any]],
    bindings: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """The inputs a ``not_sensitive`` declaration would lower (not already ``not_sensitive``):
    the approval gate's predicate and its card's facts (P42). Empty when nothing is declared
    ``not_sensitive``, or every input already is."""
    if recipe_body.get("sensitivity") != "not_sensitive":
        return []
    return [
        c for c in contributions(session, items, bindings) if c["sensitivity"] != "not_sensitive"
    ]


def published_below(session: Session, version: Any, *, now: str | None = UNREAD) -> list[str]:
    """FR-021.24: the ids of the version's publishes whose stored manifest states a value less
    strict than :func:`effective` now (a stored manifest is a snapshot). ``now`` is
    :func:`effective`'s value when the caller already read it."""
    from ..models.publish import Publish, PublishStatus

    if now is UNREAD:
        now = effective(session, version)[0]
    rows = session.execute(
        select(Publish.id, Publish.published_manifest)
        .where(Publish.version_id == version.id, Publish.status == PublishStatus.PUBLISHED)
        .order_by(Publish.completed_at, Publish.id)
    ).all()
    out: list[str] = []
    for publish_id, manifest in rows:
        stated = (manifest or {}).get("sensitivity")
        if ORDER[stated if stated in ORDER else None] < ORDER[now]:
            out.append(str(publish_id))
    return out
