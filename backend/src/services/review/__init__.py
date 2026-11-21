"""Feature 006: review queues, decisions, audits and the effective-label resolver (FTDD 006 §6).

``decision_rules`` and ``sampling`` are pure; the services own transactions.
"""

from . import delete_guards  # noqa: F401 - registers REFERENCE_CHECKERS entries at import
