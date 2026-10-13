"""MCP tool modules, one per category: THE ONLY REGISTRY (010 FTID section 3.6; FR-010.10).

Each module exposes ``register(mcp, client, ctx)``; ``server.build_server`` calls it only when the
category is enabled through ``MCP_TOOL_CATEGORIES``. miStudio kept a second map unioned by hand,
and its caller harness registered a hand-picked subset; here every guard reads this one map.

A category is registered in FOUR layers, and each has a test that fails when it is removed:
this map, ``config.VALID_CATEGORIES``, ``config.DEFAULT_CATEGORIES`` and ``MCP_TOOL_CATEGORIES`` in
``k8s/base/mcp.yaml``.
"""

from types import ModuleType

from . import (
    calibration,
    core,
    curation,
    datasets,
    detector_sets,
    exports,
    generation,
    labeling,
    operators,
    review,
    settings,
)

# category name -> modules (registration order = tools/list order)
CATEGORY_MODULES: dict[str, list[ModuleType]] = {
    "core": [core],
    "datasets": [datasets],
    "operators": [operators],
    "curation": [curation],
    "settings": [settings],
    "labeling": [labeling],
    "calibration": [calibration],
    "review": [review],
    "generation": [generation],
    "exports": [exports],
    "detector_sets": [detector_sets],
}
