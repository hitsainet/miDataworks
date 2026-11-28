"""The eight runner reasons of ``midataworks.runner/v1`` (FR-018.7; FTDD 018 §4.3, FPRD Q21 (a)).

The runner draws every reason it emits from :class:`Reason`; ``test_runner_reasons_declared.py``
(runner tests) asserts that, and ``test_runner_contract_sync.py`` asserts ``REASONS`` is the
contract file's ``reasons`` list. Standard library only: the runner image imports this module.
"""

from __future__ import annotations

from enum import StrEnum


class Reason(StrEnum):
    """Why a code rule produced no count. Host faults never count toward a step's failure share."""

    RULE_TIMEOUT = "rule_timeout"
    RULE_RESOURCE_LIMIT = "rule_resource_limit"
    RULE_WORKER_UNAVAILABLE = "rule_worker_unavailable"
    RULE_ERROR = "rule_error"
    RULE_LOAD_REFUSED = "rule_load_refused"
    RULE_OUTPUT_INVALID = "rule_output_invalid"
    RULE_INPUT_TOO_LARGE = "rule_input_too_large"
    RULE_NOT_APPROVED = "rule_not_approved"


#: The contract's ``reasons`` list, in declaration order.
REASONS: tuple[str, ...] = tuple(r.value for r in Reason)

#: Reasons caused by the host or the deployment, not by the operator's code (ENH-006 D36, FTID I8).
HOST_FAULTS: frozenset[str] = frozenset({Reason.RULE_WORKER_UNAVAILABLE, Reason.RULE_TIMEOUT})
