"""Feature 009: detector sets and the miStudio loop.

Public to other features: :func:`version_in_detector_set` (002's delete refusal reads it through
``REFERENCE_CHECKERS``, registered at import) and :func:`reward_marks.mark_from_export` (008's
export hook).
"""

from . import delete_guards  # noqa: F401 - registers REFERENCE_CHECKERS entries at import
from .delete_guards import version_in_detector_set

__all__ = ["version_in_detector_set"]
