"""miDataworks detector role -> miStudio probe-dataset role and distribution (FR-009.4).

One table in code. ``tests/unit/detector_sets/test_role_mapping_pin.py`` compares the right-hand
values with miStudio's ``DatasetRole`` and ``Distribution`` literals (a copied fixture with its origin;
the contract test reads ``MISTUDIO_REPO``). miStudio refuses ``distribution`` on any role but ``eval``,
so the two roles that carry none send none.
"""

from __future__ import annotations

from typing import Final

#: role -> (miStudio ``role``, miStudio ``distribution`` or None = not sent)
MISTUDIO_ROLE: Final[dict[str, tuple[str, str | None]]] = {
    "train": ("train", None),
    "id_test": ("eval", "in_distribution"),
    "ood_eval": ("eval", "out_of_distribution"),
    "calibration_negatives": ("calibration", None),
}

#: Human words for each role (copy rule: "calibration negatives" in full, FPRD section 4.2).
ROLE_WORDS: Final[dict[str, str]] = {
    "train": "training rows",
    "id_test": "in-distribution test",
    "ood_eval": "out-of-distribution evaluation",
    "calibration_negatives": "calibration negatives",
}


def mistudio_role(role: str) -> tuple[str, str | None]:
    """The (role, distribution) miStudio registers this role as. Unknown roles raise."""
    return MISTUDIO_ROLE[role]
