# Origin: miStudio (Onegaishimas/miStudio) backend/tests/unit/test_mcp_tools_stay_on_http.py @ c829a2cc
# Mode: adapt (docs/REUSE.md). Changes: absolute tools path; the tool inventory is empty until feature 010, stated by a flag.
"""No MCP tool may reach into the backend's application layer. It runs somewhere else.

miDataworks (ADR-016): the MCP server is its own Deployment with no DATABASE_URL, no Redis URL
and no data volume. Feature 010 adds the tools; this guard is in place first, with a negative
control recorded in the controls file. The history below is miStudio's.


⚠ **THE MCP SERVER IS A SEPARATE DEPLOYMENT, AND IT HAS NO DATABASE CREDENTIALS.**
`mistudio-mcp` is its own pod. Its env carries `MCP_AUTH_TOKEN`, `MCP_TOOL_CATEGORIES` and a
`/data` mount — and NOT `DATABASE_URL`. Constructing `src.core.config.Settings` there fails with
six missing fields, so a tool importing `SyncSessionLocal` or calling a service function cannot
run at all, however green its unit tests are.

**That is exactly what happened on 2026-10-02.** The first `millm_recalibrate_probe` re-cut the
threshold by calling `src.services.probe_recalibration.recalibrate_probe` with a sync session, and
it was:

* registered in `MILLM_CATEGORY_MODULES`, `VALID_CATEGORIES`, `DEFAULT_CATEGORIES` **and**
  `k8s/base/mcp.yaml` — all four layers, which is the rule this estate wrote after 16 tools shipped
  registered nowhere;
* asserted by a dedicated payload test, including which of the two probe ids the re-cut received;
* confirmed present in the LIVE registry of the deployed server (152 tools);

and it would have raised `ValidationError` on its first call in production. Every check passed
because every check ran in a process that HAD a database. The reachability rule says a capability
is not shipped until a test fails when its wiring is removed — this one's wiring was intact and
its *runtime* was not, which no reachability test asks about.

**The rule:** a tool reaches its service over HTTP, through the client it is handed. Every other
tool module in this server already does; at the time this file was written, the inventory of
`src.*` imports across all 20 tool modules was exactly the two lines the defect added.

`ALLOWED` is the escape hatch and is deliberately awkward: an entry is a claim that a module
genuinely needs in-process backend state, which would mean the MCP deployment needs that
configuration too — a deployment decision, not an import decision.
"""

from __future__ import annotations

import ast
import pathlib

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[2] / "src/mcp_server/tools"

#: Feature 010 builds the tools. Until then the directory holds only its package marker, and
#: this flag says so explicitly rather than letting an empty scan pass for the wrong reason.
TOOLS_EXPECTED_YET = False

#: Modules in the backend's application layer that a tool must not import. `core.config` is on the
#: list because it is the thing that raises: the MCP pod cannot build `Settings`.
FORBIDDEN_ROOTS = ("models", "services", "workers", "db", "core", "api", "ml")

#: module filename → (import path, why it is legitimately needed in-process).
ALLOWED: dict[str, dict[str, str]] = {}


def _tool_modules() -> list[pathlib.Path]:
    return sorted(p for p in TOOLS_DIR.glob("*.py") if p.name != "__init__.py")


def _backend_imports(path: pathlib.Path) -> set[str]:
    """Every import of the backend's application layer in one tool module.

    Walks the AST rather than the text, so a name inside a docstring or a comment — this file's
    own docstring quotes `SyncSessionLocal` — cannot satisfy or trip the guard. A text scan would
    report that quote as a violation, which is the wrong-occurrence trap in reverse.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # `from ...services.x import y` → level 3, module "services.x".
            # `from src.services.x import y` → level 0, module "src.services.x".
            module = node.module or ""
            root = module.split(".", 1)[0]
            if node.level and root in FORBIDDEN_ROOTS:
                found.add(module)
            elif module.startswith("src.") and module.split(".")[1] in FORBIDDEN_ROOTS:
                found.add(module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("src.") and alias.name.split(".")[1] in FORBIDDEN_ROOTS:
                    found.add(alias.name)
    return found


class TestTheGuardCanSeeItsOwnInputs:
    def test_it_finds_the_tool_directory(self) -> None:
        assert (
            TOOLS_DIR / "__init__.py"
        ).exists(), f"{TOOLS_DIR} moved; the guard would scan nothing"
        modules = _tool_modules()
        if TOOLS_EXPECTED_YET:
            assert modules, "feature 010 tools were expected but none were found"
        else:
            assert modules == [], (
                "tool modules exist: set TOOLS_EXPECTED_YET = True so an empty scan can no "
                "longer pass for the wrong reason"
            )

    def test_the_scanner_can_SEE_a_forbidden_import(self, tmp_path) -> None:
        """A scan that matches nothing asserts nothing. Three arcs here have shipped one."""
        sample = tmp_path / "sample.py"
        sample.write_text(
            "from ...core.database import SyncSessionLocal\n"
            "from ...services.probe_recalibration import recalibrate_probe\n"
            "from ..client import MiStudioClient\n"
        )
        found = _backend_imports(sample)
        assert found == {"core.database", "services.probe_recalibration"}, found

    def test_the_scanner_ignores_a_DOCSTRING_mention(self, tmp_path) -> None:
        """This file's own docstring names `SyncSessionLocal`. A text scan would call that a
        violation — the wrong-occurrence trap, inverted."""
        sample = tmp_path / "doc.py"
        sample.write_text('"""Do not import SyncSessionLocal from ...core.database."""\n')
        assert _backend_imports(sample) == set()


class TestEveryToolStaysOnItsClient:
    def test_no_tool_imports_the_backend_application_layer(self) -> None:
        """One test over every module (not a parametrize: an empty parameter set is a SKIP,
        and a skip reads as green while asserting nothing)."""
        for path in _tool_modules():
            offending = _backend_imports(path) - set(ALLOWED.get(path.name, {}))
            assert not offending, (
                f"{path.name} imports {sorted(offending)} from the backend. The MCP server is a "
                f"SEPARATE deployment with no DATABASE_URL, so this tool would raise on its first "
                f"call in production however green its tests are. Reach the service over HTTP "
                f"through the client the module is handed."
            )

    def test_the_inventory_is_still_EMPTY(self) -> None:
        """No module needs in-process backend state today, and that is the whole point: the
        escape hatch exists so an exception is a decision, and it has never been taken."""
        assert ALLOWED == {}, (
            f"ALLOWED has entries: {sorted(ALLOWED)}. Each is a claim that the MCP deployment "
            f"needs backend configuration it does not have — check k8s/base/mcp.yaml agrees."
        )
