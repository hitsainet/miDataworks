"""The suite's behaviour-key slug (ENH-005 D25, D37; FR-015.1, FR-015.7).

A behaviour key names one thing miStudio's behaviour catalogue monitors (``pii``, ``high-stakes``).
miDataworks stores it as a plain string and never a reference to a catalogue row (D25, P-25). The rule
is the same string as miStudio's ``judge_prompt_forms.SLUG_PATTERN``; ``test_behaviour_key.py`` pins
this constant, the model ``CHECK`` text and the migration literal to each other.
"""

from __future__ import annotations

import re
from typing import Annotated, Any

from pydantic import Field

BEHAVIOUR_KEY_PATTERN = r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$"
_COMPILED = re.compile(BEHAVIOUR_KEY_PATTERN)

#: A request field holding a key: a value breaking the rule is a 422 naming the pattern.
BehaviourKey = Annotated[str, Field(pattern=BEHAVIOUR_KEY_PATTERN)]


def behaviour_key_check(column: str = "behaviour_key") -> str:
    """The SQL ``CHECK`` body used by every table carrying a behaviour key."""
    return f"{column} IS NULL OR {column} ~ '{BEHAVIOUR_KEY_PATTERN}'"


def is_behaviour_key(value: str) -> bool:
    return bool(_COMPILED.fullmatch(value))


def keyed_table_args(table: str) -> tuple[Any, ...]:
    """017 FR-017.9 (migration 0039): the slug ``CHECK`` and the PARTIAL ``behaviour_key`` index
    of a table that may carry a key (recipes, generation templates and runs, label runs, review
    queues). Filtered lists answer from the index; unkeyed rows are not in it."""
    from sqlalchemy import CheckConstraint, Index, text

    return (
        CheckConstraint(behaviour_key_check(), name="behaviour_key_slug"),
        Index(
            f"ix_{table}_behaviour_key",
            "behaviour_key",
            postgresql_where=text("behaviour_key IS NOT NULL"),
        ),
    )
