"""The behaviour-dataset journey, in order (017 FR-017.56, FR-017.57, FR-017.60; TD-017.38).

One definition, read by the status route (``status_service``) and returned as ``journey`` in every
status answer, so the MCP server's ``behaviour_dataset`` howto topic can stay static and point at
the status tool (the MCP server may not import backend code, ADR-016). Nothing here names a
behaviour: each step is "done" when the behaviour's own objects (found by their ``behaviour_key``)
reach a count, whatever the behaviour is.

Each step names the MCP tool an agent calls to take it and the approval actions it may wait for
(``core.agent_origin.APPROVAL_ACTIONS``; pinned by ``tests/unit/mcp/test_howto_journey.py``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Step:
    key: str
    title: str
    tool: str
    #: Other tools the step may use instead or as well (still in the live registry).
    also: tuple[str, ...]
    #: Plain-language "done when" (said in the answer).
    done_when: str
    done: Callable[[Mapping[str, int]], bool]
    approvals: tuple[str, ...] = field(default=())


def _at_least(name: str, n: int = 1) -> Callable[[Mapping[str, int]], bool]:
    return lambda counts: counts.get(name, 0) >= n


#: FPRD §2.4: resolve → rule sets → templates → plan → generation → build → second opinion →
#: agreement → review → sets → check → send. Value-directed splits, the proof and the gates are
#: parts of the build and the sets (recorded on the version and the send), not steps of their own.
JOURNEY: tuple[Step, ...] = (
    Step(
        "resolve",
        "Read the behaviour's definition",
        "dataworks_resolve_behaviour",
        (),
        "a version or a detector set records the definition",
        _at_least("definitions_recorded"),
    ),
    Step(
        "rule_set",
        "Save the behaviour's rule set (its rule signals)",
        "dataworks_save_rule_set",
        ("dataworks_derive_rule_set", "dataworks_validate_rule_set", "dataworks_clone_rule_set"),
        "a rule set carries the key",
        _at_least("rule_sets"),
    ),
    Step(
        "templates",
        "Save the generation templates",
        "dataworks_save_generation_template",
        ("dataworks_clone_generation_template",),
        "a generation template carries the key",
        _at_least("generation_templates"),
    ),
    Step(
        "recipe",
        "Save the recipes (the carrier plan and the build)",
        "dataworks_save_recipe",
        ("dataworks_clone_recipe", "dataworks_validate_recipe"),
        "a recipe carries the key",
        _at_least("recipes"),
    ),
    Step(
        "plan",
        "Build the carrier plan version",
        "dataworks_build_version",
        ("dataworks_plan_build",),
        "a version built from one of the behaviour's recipes",
        _at_least("versions"),
    ),
    Step(
        "generation",
        "Generate carriers, one run per generator",
        "dataworks_start_generation",
        ("dataworks_plan_generation", "dataworks_get_generation_run"),
        "a generation run with the key completed",
        _at_least("generation_runs_completed"),
        ("millm_model_load",),
    ),
    Step(
        "build",
        "Build the labelled version (insertion, renderings, labels, value-directed split)",
        "dataworks_build_version",
        ("dataworks_get_version",),
        "two versions built from the behaviour's recipes (the plan and the build)",
        _at_least("versions", 2),
    ),
    Step(
        "second_opinion",
        "Score a stratified sample with a second opinion",
        "dataworks_start_label_run",
        ("dataworks_plan_label_run", "dataworks_get_label_run"),
        "a label run with the key completed",
        _at_least("label_runs_completed"),
        ("paid_classifier_run", "agent_label_rows"),
    ),
    Step(
        "agreement",
        "Read the agreement by stratum",
        "dataworks_get_label_run_agreement",
        (),
        "a label run with the key completed (the agreement is read from it)",
        _at_least("label_runs_completed"),
    ),
    Step(
        "review",
        "Send the disagreements to review",
        "dataworks_review_disagreements",
        ("dataworks_get_review_queue",),
        "a review queue carries the key",
        _at_least("review_queues"),
    ),
    Step(
        "sets",
        "Create the detector sets (main and one per sub-question)",
        "dataworks_create_detector_set",
        ("dataworks_update_detector_set",),
        "a detector set carries the key",
        _at_least("detector_sets"),
    ),
    Step(
        "check",
        "Check the sets",
        "dataworks_check_detector_set",
        (),
        "a send of the behaviour's sets records its checks",
        _at_least("detector_sends"),
    ),
    Step(
        "send",
        "Send the sets to miStudio",
        "dataworks_send_detector_sets",
        ("dataworks_get_detector_send",),
        "a send of the behaviour's sets completed",
        _at_least("detector_sends_completed"),
        ("hub_push",),
    ),
)


def journey_tools() -> list[str]:
    """Every tool the journey names, in order, once each."""
    return list(dict.fromkeys(name for s in JOURNEY for name in (s.tool, *s.also)))


def walk(key: str, counts: Mapping[str, int]) -> dict[str, Any]:
    """The journey for one behaviour's counts: each step done or not, the next missing step with
    the tool and the argument to pass, and every approval the remaining steps may need."""
    steps: list[dict[str, Any]] = [
        {
            "step": s.key,
            "title": s.title,
            "tool": s.tool,
            "also": list(s.also),
            "done_when": s.done_when,
            "done": s.done(counts),
            "approvals": list(s.approvals),
        }
        for s in JOURNEY
    ]
    pending = [s for s in steps if not s["done"]]
    nxt = pending[0] if pending else None
    return {
        "journey": steps,
        "next": (
            {
                "step": nxt["step"],
                "tool": nxt["tool"],
                "args": {"behaviour_key": key},
                "why": f"Not done yet: {nxt['done_when']}.",
            }
            if nxt
            else None
        ),
        "approvals_ahead": list(
            dict.fromkeys(a for s in JOURNEY if not s.done(counts) for a in s.approvals)
        ),
        "complete": nxt is None,
    }
