"""miDataworks detector role -> miWorkbench probe-dataset role and distribution (FR-009.4).

One table in code. ``backend/tests/contract/test_mistudio_probe_dataset_contract.py`` compares the
right-hand values with miWorkbench's ``DatasetRole`` and ``Distribution`` literals (a copied fixture
with its origin; the contract test reads ``MISTUDIO_REPO``). miWorkbench refuses ``distribution`` on any role but ``eval``,
so the two roles that carry none send none.

``extra_eval`` (ENH-006 D116 G7) is an evaluation view beside the set's required roles, such as a
human-graded test set. It goes as ``in_distribution``, so it never counts toward rung 2 or the choice of
probe (miWorkbench selects on out-of-distribution sets when a run has any, and a set must).
"""

from __future__ import annotations

from typing import Final

#: role -> (miWorkbench ``role``, miWorkbench ``distribution`` or None = not sent)
MISTUDIO_ROLE: Final[dict[str, tuple[str, str | None]]] = {
    "train": ("train", None),
    "id_test": ("eval", "in_distribution"),
    "ood_eval": ("eval", "out_of_distribution"),
    "calibration_negatives": ("calibration", None),
    "extra_eval": ("eval", "in_distribution"),
}

#: Human words for each role (copy rule: "calibration negatives" in full, FPRD section 4.2).
ROLE_WORDS: Final[dict[str, str]] = {
    "train": "training rows",
    "id_test": "in-distribution test",
    "ood_eval": "out-of-distribution evaluation",
    "calibration_negatives": "calibration negatives",
    "extra_eval": "extra evaluation",
}


def leakage_counts(role_a: str, role_b: str) -> bool:
    """Whether rows shared by these two roles are leakage (D-4, and the repair it names). A
    human-graded view legitimately shares rows with held-out sets, because the set's questions are
    asked of the rows before they are split; only its rows in training would flatter a probe."""
    if "extra_eval" not in (role_a, role_b):
        return True
    return "train" in (role_a, role_b)


def mistudio_role(role: str) -> tuple[str, str | None]:
    """The (role, distribution) miWorkbench registers this role as. Unknown roles raise."""
    return MISTUDIO_ROLE[role]
