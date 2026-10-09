"""Feature 006: calibration of labelers against human labels (FTDD 006 section 6).

The pure layer (``constants``, ``metrics``, ``ceiling``, ``checks``, ``registry``, ``verdict``)
does no input/output; the services (``set_service``, ``record_service``, ``status_service``,
``target_service``) own transactions and call it.
"""

from ..review import delete_guards  # noqa: F401 - registers REFERENCE_CHECKERS entries
