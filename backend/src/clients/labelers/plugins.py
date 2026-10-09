"""Plugin classifiers: ``midataworks.classifiers`` entry points behind the allowlist (FR-005.8; T-20).

Listing reads package metadata only. An entry point is IMPORTED only when its triple
(distribution, version, entry-point name) is allowed in feature 003's allowlist
(``dw_operator_allowlist``, read fresh on every check) — the same administrator decision that
gates third-party operators (R-03.15). The loaded object must implement ``ClassifierClient``; a
non-conforming plugin is refused at load.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...operators.plugins import EntryPointInfo, list_entry_points
from .base import ClassifierClient

GROUP = "midataworks.classifiers"


class PluginRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def find(name: str, group: str = GROUP) -> EntryPointInfo:
    for info in list_entry_points(group):
        if info.name == name:
            return info
    raise PluginRefused("plugin_not_found", f"No classifier entry point named {name!r}.")


def load_classifier(
    name: str,
    options: dict[str, Any],
    *,
    allowed: Callable[[], set[tuple[str, str, str]]] | None = None,
    group: str = GROUP,
) -> ClassifierClient:
    """Import an ALLOWED classifier entry point and build its client."""
    info = find(name, group)
    if allowed is None:
        from ...operators.allowlist import read_allowed

        allowed = read_allowed
    if info.triple not in allowed():
        raise PluginRefused(
            "plugin_not_allowed",
            f"The classifier {name!r} from {info.distribution} {info.distribution_version} is "
            "not on the allowlist. An administrator allows it on the Operators screen.",
        )
    factory = info.entry_point.load()
    client = factory(options) if callable(factory) else factory
    if not isinstance(client, ClassifierClient):
        raise PluginRefused(
            "plugin_invalid",
            f"{info.value} did not produce a classifier (an object with score(row)).",
        )
    return client
