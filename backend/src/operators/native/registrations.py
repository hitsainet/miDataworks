"""Product features' native operator modules (FTDD 003 section 6.3, step 1).

Features 004, 005, 007 and 009 add one import line per operator module here; each module exposes
``OPERATORS``, a tuple of operator classes. This feature ships none (T-q): its only native
operators are the test fixtures, returned by :func:`fixture_operators` under the test flag only.
"""

from __future__ import annotations

from ...core.config import get_settings
from . import generation, threshold_labeler
from .curation import CURATION_OPERATORS
from .detector import DETECTOR_OPERATORS

#: Product operator classes, extended by features 004, 005, 007 and 009.
PRODUCT_OPERATORS: tuple[type, ...] = (
    *threshold_labeler.OPERATORS,
    *CURATION_OPERATORS,
    *generation.OPERATORS,  # feature 007
    *DETECTOR_OPERATORS,  # feature 009 (hard_negative_miner, feature_filter)
)


def fixture_operators() -> tuple[type, ...]:
    """The test-only operators, when ``OPERATOR_TEST_FIXTURES`` is set; else nothing."""
    if not get_settings().operator_test_fixtures:
        return ()
    from .fixtures import FIXTURE_OPERATORS

    return FIXTURE_OPERATORS
