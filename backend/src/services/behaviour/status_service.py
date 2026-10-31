"""A behaviour's dataset status (017 FR-017.56; FTDD 017 §5.2, TD-017.38).

Everything is derived, at read time, from the objects that carry the behaviour key (rule sets, code
rules, generation templates, recipes, generation runs, label runs, review queues, detector sets)
and the objects made from them (versions built from the behaviour's recipes, sends of its sets).
Nothing is stored and nothing is written: this is a read of other objects' keys, not a behaviour
registry (P-25). The ordered ``journey``, the next missing step and the approvals still ahead come
from ``journey.walk``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ...models.code_rule import CodeRule
from ...models.detector_send import DetectorSend
from ...models.detector_set import DetectorSet
from ...models.generation import GenerationRun, GenerationTemplate
from ...models.label_run import LabelRun
from ...models.recipe import Recipe, RecipeRevision
from ...models.review import ReviewQueue
from ...models.rule_set import RuleSet
from ...models.version import Version
from . import journey

#: The most recent objects of each kind shown in full; the counts are always whole.
SHOWN = 20


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _versions_query(key: str) -> Any:
    """Versions built from one of the behaviour's recipes, or recording the behaviour's
    definition (a carrier plan records it with its configuration, and a child inherits both;
    FR-017.55), so a plan built from a recipe saved without the key still counts."""
    keyed_revisions = (
        select(RecipeRevision.id)
        .join(Recipe, Recipe.id == RecipeRevision.recipe_id)
        .where(Recipe.behaviour_key == key)
    )
    return select(Version).where(
        Version.deleted_at.is_(None),
        or_(
            Version.recipe_revision_id.in_(keyed_revisions),
            Version.behaviour_definition["key"].astext == key,
        ),
    )


def status(session: Session, key: str) -> dict[str, Any]:
    """The behaviour's objects, what each last said, the next missing step and the approvals the
    remaining steps may need. Reads only."""

    def rows(query: Any, order: Any) -> tuple[list[Any], int]:
        total = int(
            session.execute(select(func.count()).select_from(query.subquery())).scalar_one()
        )
        return list(session.execute(query.order_by(order).limit(SHOWN)).scalars()), total

    rule_sets, n_rule_sets = rows(
        select(RuleSet).where(RuleSet.behaviour_key == key), RuleSet.created_at.desc()
    )
    code_rules, n_code_rules = rows(
        select(CodeRule).where(CodeRule.behaviour_key == key), CodeRule.created_at.desc()
    )
    templates, n_templates = rows(
        select(GenerationTemplate).where(GenerationTemplate.behaviour_key == key),
        GenerationTemplate.created_at.desc(),
    )
    recipes, n_recipes = rows(
        select(Recipe).where(Recipe.behaviour_key == key), Recipe.updated_at.desc()
    )
    gen_runs, n_gen_runs = rows(
        select(GenerationRun).where(GenerationRun.behaviour_key == key),
        GenerationRun.created_at.desc(),
    )
    versions, n_versions = rows(_versions_query(key), Version.created_at.desc())
    label_runs, n_label_runs = rows(
        select(LabelRun).where(LabelRun.behaviour_key == key), LabelRun.created_at.desc()
    )
    queues, n_queues = rows(
        select(ReviewQueue).where(ReviewQueue.behaviour_key == key), ReviewQueue.created_at.desc()
    )
    sets, n_sets = rows(
        select(DetectorSet).where(DetectorSet.behaviour_key == key), DetectorSet.created_at.desc()
    )
    keyed_sets = select(DetectorSet.id).where(DetectorSet.behaviour_key == key)
    sends, n_sends = rows(
        select(DetectorSend).where(DetectorSend.set_id.in_(keyed_sets)),
        DetectorSend.created_at.desc(),
    )

    def count(model: Any, *where: Any) -> int:
        return int(
            session.execute(select(func.count()).select_from(model).where(*where)).scalar_one()
        )

    definitions = count(
        Version, Version.deleted_at.is_(None), Version.behaviour_definition["key"].astext == key
    ) + count(DetectorSet, DetectorSet.behaviour_definition["key"].astext == key)
    counts = {
        "definitions_recorded": definitions,
        "rule_sets": n_rule_sets,
        "code_rules": n_code_rules,
        "generation_templates": n_templates,
        "recipes": n_recipes,
        "generation_runs": n_gen_runs,
        "generation_runs_completed": count(
            GenerationRun, GenerationRun.behaviour_key == key, GenerationRun.state == "completed"
        ),
        "versions": n_versions,
        "label_runs": n_label_runs,
        "label_runs_completed": count(
            LabelRun, LabelRun.behaviour_key == key, LabelRun.state == "completed"
        ),
        "review_queues": n_queues,
        "detector_sets": n_sets,
        "detector_sends": n_sends,
        "detector_sends_completed": count(
            DetectorSend, DetectorSend.set_id.in_(keyed_sets), DetectorSend.state == "completed"
        ),
    }
    objects = {
        "rule_sets": [
            {
                "name": r.name,
                "archived": r.archived_at is not None,
                "created_at": _iso(r.created_at),
            }
            for r in rule_sets
        ],
        "code_rules": [
            {
                "name": r.name,
                "archived": r.archived_at is not None,
                "created_at": _iso(r.created_at),
            }
            for r in code_rules
        ],
        "generation_templates": [
            {"id": t.id, "name": t.name, "version": t.version, "kind": t.kind} for t in templates
        ],
        "recipes": [{"id": r.id, "name": r.name, "archived": r.archived} for r in recipes],
        "generation_runs": [
            {"id": r.id, "mode": r.mode, "state": r.state, "counts": r.counts} for r in gen_runs
        ],
        "versions": [
            {
                "id": v.id,
                "dataset_id": v.dataset_id,
                "number": v.number,
                "state": v.state,
                "total_rows": v.total_rows,
                "census_cells_short": (v.behaviour_census or {}).get("cells_short"),
                "warnings": len(v.warnings or []),
            }
            for v in versions
        ],
        "label_runs": [
            {"id": r.id, "kind": r.kind, "state": r.state, "counts": r.counts} for r in label_runs
        ],
        "review_queues": [{"id": q.id, "kind": q.kind, "state": q.state} for q in queues],
        "detector_sets": [{"id": s.id, "name": s.name, "archived": s.archived} for s in sets],
        "detector_sends": [
            {
                "id": s.id,
                "set_id": s.set_id,
                "state": s.state,
                "checks_refused": sum(1 for c in s.checks or [] if c.get("outcome") == "refused"),
                "checks": len(s.checks or []),
            }
            for s in sends
        ],
    }
    return {
        "behaviour_key": key,
        "counts": counts,
        "objects": objects,
        "shown_per_kind": SHOWN,
        **journey.walk(key, counts),
        "note": (
            "Derived from the objects that carry this key; nothing is stored. Approvals are the "
            "operator's: an agent's gated call returns a pending approval to poll."
        ),
    }
