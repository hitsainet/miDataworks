"""The two hand-kept views of the MCP registry agree (FR-010.10; FTID section 8.2).

``config.VALID_CATEGORIES`` cannot be derived from ``tools.CATEGORY_MODULES`` (a cycle), so this
pins them. miStudio's config comment cited a ``TestTheCategoryListsAgree`` no file contained.
"""

from __future__ import annotations

from src.mcp_server.config import DEFAULT_CATEGORIES, VALID_CATEGORIES
from src.mcp_server.tools import CATEGORY_MODULES


def test_valid_categories_equal_the_registry() -> None:
    assert set(VALID_CATEGORIES) == set(CATEGORY_MODULES)


def test_defaults_are_valid() -> None:
    defaults = {c.strip() for c in DEFAULT_CATEGORIES.split(",") if c.strip()}
    assert defaults <= set(VALID_CATEGORIES)


def test_all_eleven_categories_are_on_by_default() -> None:
    """FR-010.11: every category is enabled unless the deployment removes it."""
    defaults = {c.strip() for c in DEFAULT_CATEGORIES.split(",") if c.strip()}
    assert defaults == set(CATEGORY_MODULES)
    assert len(defaults) == 11
