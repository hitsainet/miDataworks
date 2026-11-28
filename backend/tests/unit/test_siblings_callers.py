"""Every cross-repo check routes an absent sibling through ``siblings.absent`` (2026-11-21).

Before, each caller read ``MIDATAWORKS_REQUIRE_CROSS_REPO_CHECKS`` inline and none read the
suite-wide ``MISTUDIO_REQUIRE_CROSS_REPO_CHECKS``, so a suite-wide required run skipped them all.
``test_siblings_lookup.py`` pins ``absent`` itself; this pins the CALLERS: each one, pointed at an
empty sibling, skips with no switch and fails with only the suite-wide switch set. A caller that
went back to an inline read of this repository's switch, or to a bare ``pytest.skip``, turns red.
``test_every_lookup_caller_is_covered`` keeps the table complete.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.support import siblings
from tests.support.source_tree import REPO

Call = Callable[[pytest.MonkeyPatch, Path], None]

#: Written out, not read from ``siblings.REQUIRE_SWITCHES``: a switch dropped there must turn
#: these red, not quietly drop their cases.
SWITCHES = ("MIDATAWORKS_REQUIRE_CROSS_REPO_CHECKS", "MISTUDIO_REQUIRE_CROSS_REPO_CHECKS")


def _behaviour_key(mp: pytest.MonkeyPatch, empty: Path) -> None:
    from tests.unit import test_behaviour_key as mod

    mp.setenv("MIWORKBENCH_REPO", str(empty))
    mod.test_mistudios_slug_rule_is_the_same_when_the_sibling_is_present()


def _recorded_meaning(mp: pytest.MonkeyPatch, empty: Path) -> None:
    from tests.unit import test_recorded_definition_meaning as mod

    mp.setenv("MIWORKBENCH_REPO", str(empty))
    mod.test_the_limit_is_miworkbenchs_catalogue_limit_when_the_sibling_is_present()


def _scores_fields(mp: pytest.MonkeyPatch, empty: Path) -> None:
    from tests.contract import test_miworkbench_scores_fields as mod

    mp.setenv("MIWORKBENCH_REPO", str(empty))
    mod._fields()


def _spot_check(mp: pytest.MonkeyPatch, empty: Path) -> None:
    from tests.unit.contract import test_spot_check_schema_vendored as mod

    mp.setenv("MIWORKBENCH_REPO", str(empty))
    mod.test_the_vendored_copy_equals_mistudios_file()


def _dataset_version(mp: pytest.MonkeyPatch, empty: Path) -> None:
    from tests.unit.contract import test_dataset_version_vendored as mod

    mp.setenv("MIFORGE_REPO", str(empty))
    mod._sibling_copy("MIFORGE_REPO")


def _runner(mp: pytest.MonkeyPatch, empty: Path) -> None:
    from tests.unit.contract import test_runner_contract_vendored as mod

    mp.setenv("MISERVER_REPO", str(empty))
    mod._copy("MISERVER_REPO", mod.FILES[0])


def _rule_signal(mp: pytest.MonkeyPatch, empty: Path) -> None:
    from tests.unit.contract import test_rule_signal_cases_vendored as mod

    mp.setenv("MISERVER_REPO", str(empty))
    mod._copy("MISERVER_REPO", mod.FILES[0])


def _probe_dataset(mp: pytest.MonkeyPatch, empty: Path) -> None:
    from tests.contract import test_mistudio_probe_dataset_contract as mod

    mp.setattr(mod, "SCHEMA", empty / "probe_monitor.py")
    mod._schema()


def _catalogue(mp: pytest.MonkeyPatch, empty: Path) -> None:
    from tests.contract import test_mistudio_catalogue_contract as mod

    mod._tree(empty / "behaviours.py")


def _proxy_no_checkout(mp: pytest.MonkeyPatch, empty: Path) -> None:
    from tests.contract import test_miworkbench_proxy_paths_are_served as mod

    mp.setattr(mod, "MIWORKBENCH", empty)
    mod.test_every_mistudio_proxy_call_is_served_here()


def _proxy_no_tools(mp: pytest.MonkeyPatch, empty: Path) -> None:
    from tests.contract import test_miworkbench_proxy_paths_are_served as mod

    (empty / "backend" / "src" / "mcp_server" / "tools").mkdir(parents=True)
    mp.setattr(mod, "MIWORKBENCH", empty)
    mod.test_every_mistudio_proxy_call_is_served_here()


def _result_language(mp: pytest.MonkeyPatch, empty: Path) -> None:
    from tests.unit.contract import test_result_language_vendored as mod

    mp.setenv("MIASSIST_REPO", str(empty))
    mod.test_the_vendored_copy_equals_miassists_file()


#: Module (repo-relative) -> its absent-sibling paths.
CALLERS: dict[str, tuple[Call, ...]] = {
    "backend/tests/unit/test_behaviour_key.py": (_behaviour_key,),
    "backend/tests/unit/test_recorded_definition_meaning.py": (_recorded_meaning,),
    "backend/tests/contract/test_miworkbench_scores_fields.py": (_scores_fields,),
    "backend/tests/unit/contract/test_spot_check_schema_vendored.py": (_spot_check,),
    "backend/tests/unit/contract/test_dataset_version_vendored.py": (_dataset_version,),
    "backend/tests/unit/contract/test_runner_contract_vendored.py": (_runner,),
    "backend/tests/unit/contract/test_rule_signal_cases_vendored.py": (_rule_signal,),
    "backend/tests/contract/test_mistudio_probe_dataset_contract.py": (_probe_dataset,),
    "backend/tests/contract/test_mistudio_catalogue_contract.py": (_catalogue,),
    "backend/tests/contract/test_miworkbench_proxy_paths_are_served.py": (
        _proxy_no_checkout,
        _proxy_no_tools,
    ),
    # 022: the lookup is in the support module; the byte-identity test is its absent path.
    "backend/tests/support/result_language.py": (_result_language,),
}
CASES = [
    pytest.param(call, id=call.__name__.strip("_")) for calls in CALLERS.values() for call in calls
]


def _outcome(call: Callable[[], object]) -> tuple[str, str]:
    """Caught explicitly: a skip escaping ``pytest.raises`` would mark THIS test skipped (exit 0)."""
    try:
        call()
    except pytest.skip.Exception as exc:
        return "skip", str(exc)
    except pytest.fail.Exception as exc:
        return "fail", str(exc)
    return "none", ""


@pytest.fixture
def no_switch(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for switch in {*SWITCHES, *siblings.REQUIRE_SWITCHES}:
        monkeypatch.delenv(switch, raising=False)
    return monkeypatch


@pytest.mark.parametrize("call", CASES)
def test_an_absent_sibling_skips_loudly_without_a_switch(
    call: Call, no_switch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kind, message = _outcome(lambda: call(no_switch, tmp_path))
    assert kind == "skip", f"{call.__name__}: an absent sibling must skip, got {kind}"
    assert message.startswith("CROSS-REPO: "), message


@pytest.mark.parametrize("switch", SWITCHES)
@pytest.mark.parametrize("call", CASES)
def test_an_absent_sibling_fails_under_either_switch_alone(
    call: Call, switch: str, no_switch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    no_switch.setenv(switch, "1")
    kind, message = _outcome(lambda: call(no_switch, tmp_path))
    assert kind == "fail", f"{call.__name__}: {switch}=1 must turn an absent sibling into a failure"
    assert message.startswith("CROSS-REPO (required"), message


def lookup_callers(root: Path, paths: list[Path]) -> set[str]:
    """Repo-relative paths of the modules that import ``sibling_repo`` from the lookup module."""
    found = set()
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "tests.support.siblings"
                and any(alias.name == "sibling_repo" for alias in node.names)
            ):
                found.add(path.relative_to(root).as_posix())
    return found


def test_every_lookup_caller_is_covered() -> None:
    paths = sorted((REPO / "backend" / "tests").rglob("*.py"))
    assert len(paths) > 200, f"the scan read only {len(paths)} files"
    assert lookup_callers(REPO, paths) == set(CALLERS)


def test_the_caller_scan_sees_a_planted_import(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(
        "def f():\n    from tests.support.siblings import sibling_repo\n"
    )
    (tmp_path / "b.py").write_text("from tests.support.siblings import absent\n")
    assert lookup_callers(tmp_path, [tmp_path / "a.py", tmp_path / "b.py"]) == {"a.py"}
