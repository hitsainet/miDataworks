"""Exactly one Alembic head, always (ADR-002; Foundation task 3.4)."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND = Path(__file__).resolve().parents[2]


def _script() -> ScriptDirectory:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    return ScriptDirectory.from_config(config)


def test_there_is_exactly_one_head() -> None:
    heads = _script().get_heads()
    assert len(heads) == 1, f"Alembic has {len(heads)} heads: {heads}. Merge them."


def test_the_chain_reaches_the_baseline() -> None:
    revisions = list(_script().walk_revisions())
    assert revisions[-1].revision == "0001"
    assert revisions[-1].down_revision is None
