"""Phase-0 goldens of the identifier and text operators (016 FTDD section 11.4; FTASKS 0.2, 4.6).

``tests/fixtures/rules/golden/recipes.json`` holds stored recipes and their inputs; each recipe's
steps run in order through 003's REAL executor (``run_operator`` -> ``execute_in_process``), with the
operator version PINNED in the recipe, exactly as a stored recipe pins it. ``expected.json`` holds
what ``main`` produced before feature 016 changed anything: every output row and its row hash.

Recording (only ever on unchanged ``main``, never to make a failing test pass)::

    cd backend && python -m tests.support.make_rule_goldens
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pyarrow as pa

from src.core.canonical_json import canonical_sha256

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "rules" / "golden"
RECIPES_PATH = GOLDEN_DIR / "recipes.json"
EXPECTED_PATH = GOLDEN_DIR / "expected.json"


def _input_table(spec: dict[str, Any]) -> tuple[pa.Table, dict[str, str]]:
    from tests.fixtures.humor_pool import with_system_columns

    rows = spec["rows"]
    if isinstance(rows, str):
        assert rows.startswith("range:"), rows
        rows = [{"idx": i} for i in range(int(rows.split(":", 1)[1]))]
    content = tuple(spec["content"])
    roles = dict.fromkeys(content, "content")
    return with_system_columns(rows, content=content, source_id="src-golden"), roles


def run_recipe(recipe: dict[str, Any], inputs: dict[str, Any]) -> list[dict[str, Any]]:
    """The recipe's output rows, every column, in output order."""
    from tests.support.curation_fixtures import run_operator

    table, roles = _input_table(inputs[recipe["input"]])
    for name, version, params in recipe["steps"]:
        ran = run_operator(name, params, table, roles, version=version)
        roles = dict(ran.meta["output_column_roles"])
        table = ran.output
    rows: list[dict[str, Any]] = table.to_pylist()
    return rows


def manifest_hashes() -> dict[str, str]:
    """``name@version -> manifest_hash`` of every native operator of the production registry."""
    from src.operators.registry import OperatorRegistry

    production = OperatorRegistry.build(catalogues=(), entry_points=())
    return {
        e.ref: str(e.manifest_hash)
        for e, _ in production.entries()
        if e.provider == "native" and e.manifest_hash
    }


def record() -> dict[str, Any]:
    """Run every recipe on a throwaway data directory; return the expected document."""
    from src.core.config import get_settings

    spec = json.loads(RECIPES_PATH.read_text(encoding="utf-8"))
    settings = get_settings()
    previous = settings.data_dir
    with tempfile.TemporaryDirectory() as tmp:
        settings.data_dir = Path(tmp)
        try:
            out: dict[str, Any] = {}
            for recipe in spec["recipes"]:
                rows = run_recipe(recipe, spec["inputs"])
                out[recipe["id"]] = {
                    "row_hashes": [canonical_sha256(r) for r in rows],
                    "rows": rows,
                }
        finally:
            settings.data_dir = previous
    return {"recipes": out, "manifest_hashes": manifest_hashes()}
