"""Features' preflight checks, registered by import (FR-005.54).

Each feature appends one import line here; the imported module calls
``label_run_preflight.register(check)``. Feature 007 registers ``JUDGE_IS_GENERATOR``
(``services/generation/independence.py``); its reachability test asserts it is present in
``PREFLIGHT_CHECKS``.
"""

from __future__ import annotations

#: The modules imported for their registrations, in order (read by the reachability test).
REGISTRATION_MODULES: tuple[str, ...] = (
    # feature 007: JUDGE_IS_GENERATOR over versions holding generated rows (T-35)
    "src.services.generation.independence",
)

for _module in REGISTRATION_MODULES:
    __import__(_module)
