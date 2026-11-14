"""Product features' native operator modules (FTDD 003 section 6.3, step 1).

Features 004, 005, 007 and 009 add one import line per operator module here; each module exposes
``OPERATORS``, a tuple of operator classes. This feature ships none (T-q): its only native
operators are the test fixtures, returned by :func:`fixture_operators` under the test flag only.
"""

from __future__ import annotations

from ...core.config import get_settings
from . import generation, threshold_labeler
from .behaviour import BEHAVIOUR_OPERATORS
from .curation import CURATION_OPERATORS
from .detector import DETECTOR_OPERATORS
from .identifiers import IDENTIFIER_OPERATORS
from .rules import RULE_OPERATORS

#: Product operator classes, extended by features 004, 005, 007 and 009.
PRODUCT_OPERATORS: tuple[type, ...] = (
    *threshold_labeler.OPERATORS,
    *CURATION_OPERATORS,
    *generation.OPERATORS,  # feature 007
    *DETECTOR_OPERATORS,  # feature 009 (hard_negative_miner, feature_filter)
    *IDENTIFIER_OPERATORS,  # e2e run gaps 2026-10-21 (synth_identifier ... drop_columns)
    *RULE_OPERATORS,  # feature 016 (regex_match@2), AFTER regex_match@1: current_version is last
    *BEHAVIOUR_OPERATORS,  # feature 017 (carrier_plan .. screen_filter, slot_siblings)
)


def fixture_operators() -> tuple[type, ...]:
    """The test-only operators, when ``OPERATOR_TEST_FIXTURES`` is set; else nothing."""
    if not get_settings().operator_test_fixtures:
        return ()
    from .fixtures import FIXTURE_OPERATORS

    return FIXTURE_OPERATORS
