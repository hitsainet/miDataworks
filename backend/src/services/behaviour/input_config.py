"""017's step-parameter wiring (FTID §7.10, §16 X-21): which behaviour operators read ``"@input"``,
which may pin a rule set, and (slice 6) the ``sample`` resolver that fills a stratified step's
instructed rate from the runtime settings.

Imported once by ``services/rules/step_resolution.py`` (its last line, which ``services/step_params.py``
imports at its own end), so the registrations exist wherever a step is validated, identified or
dispatched. The registries are read live: removing a line here
turns the reachability tests red (``tests/unit/operators/test_behaviour_operators_registered.py``).
"""

from __future__ import annotations

from ..labeling.stratified import instructed_params
from ..rules.step_resolution import register_pinning_operator
from ..step_params import register_input_params, register_step_param_resolver

#: operator -> the parameters that may say ``"@input"`` (``definition`` reads the recorded
#: definition; any other reads that field of the recorded configuration).
INPUT_PARAMS: dict[str, tuple[str, ...]] = {
    "insert_element": ("vocabulary", "definition"),
    "derive_labels": ("vocabulary",),
    "slot_siblings": ("vocabulary",),
}
#: Operators whose steps may pin a rule set (renderings, decoys, the conflict scan's expression).
PINNING: tuple[str, ...] = ("insert_element", "derive_labels", "slot_siblings")

for _operator, _names in INPUT_PARAMS.items():
    register_input_params(_operator, _names)
for _operator in PINNING:
    register_pinning_operator(_operator)
#: 017 slice 6: a stratified ``sample`` step's instructed rate and minimum come from the runtime
#: settings at build time when the step names none (``services/labeling/stratified.py``).
register_step_param_resolver("sample", instructed_params)
