"""Entry-point plugins: list from metadata, import only when allowed (FR-003.10, FR-003.11, FR-003.16).

Importing a module runs its code, so a non-allowed entry point is NEVER imported.
:func:`list_entry_points` reads package metadata only (``ep.dist``, ``ep.name``, ``ep.value``);
:func:`load_allowed` is the ONLY function in the package that calls ``ep.load()``
(``test_plugins.py`` walks the AST to keep it so).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from typing import Any

GROUP = "midataworks.operators"


@dataclass(frozen=True)
class EntryPointInfo:
    distribution: str
    distribution_version: str
    name: str
    value: str
    entry_point: EntryPoint

    @property
    def triple(self) -> tuple[str, str, str]:
        return (self.distribution, self.distribution_version, self.name)


def list_entry_points(group: str = GROUP) -> list[EntryPointInfo]:
    """Every entry point in ``group``, from metadata only; nothing is imported."""
    found = []
    for ep in entry_points(group=group):
        dist = ep.dist
        found.append(
            EntryPointInfo(
                distribution=dist.name if dist is not None else "unknown",
                distribution_version=dist.version if dist is not None else "unknown",
                name=ep.name,
                value=ep.value,
                entry_point=ep,
            )
        )
    return sorted(found, key=lambda e: e.triple)


class PluginLoadError(Exception):
    pass


def load_allowed(info: EntryPointInfo) -> list[Any]:
    """Import an ALLOWED entry point and return the operator instances it yields.

    The entry point must name a callable returning an iterable of operator instances (objects
    with a ``manifest`` and a ``run``). Anything else is a load failure.
    """
    target = info.entry_point.load()
    produced = target() if callable(target) else target
    if not isinstance(produced, Iterable):
        raise PluginLoadError(f"{info.value} returned {type(produced).__name__}, not operators")
    operators = list(produced)
    for op in operators:
        if not hasattr(op, "manifest") or not callable(getattr(op, "run", None)):
            raise PluginLoadError(
                f"{info.value} yielded {type(op).__name__}, which has no manifest and run()"
            )
    return operators
