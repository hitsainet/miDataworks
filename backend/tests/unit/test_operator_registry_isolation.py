"""Guard for conftest's ``_restore_operator_registry``: one test's registry never reaches the next.

``operators.registry.current()`` installs the production registry as feature 002's operator port
on first use and nothing undoes it. Before the fixture, any test reaching such a route (the
malformed-id sweep does) left later tests on that worker with the real registry, and
``test_production_with_no_registry_refuses_every_operator`` failed by file order (2026-10-19).

The two tests run in file order on one worker (``--dist loadfile``): the first installs the real
registry, the second must still see the process default.
"""

from __future__ import annotations

from src.operators import registry as process_registry
from src.services import operator_port


def test_1_installing_the_process_registry_changes_the_port() -> None:
    installed = process_registry.current()
    assert operator_port.registry() is installed
    assert not isinstance(operator_port.registry(), operator_port.NoOperatorsInstalled)


def test_2_the_next_test_starts_without_it() -> None:
    assert isinstance(operator_port.registry(), operator_port.NoOperatorsInstalled)
    assert process_registry._process is None
