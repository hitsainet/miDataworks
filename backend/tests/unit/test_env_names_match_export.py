"""The hand-kept rename table against the generated export (feature 020; CF-3, CF-25 m13;
FTASKS 3.4).

``core/env_names.RENAMES`` and the export's ``env`` records are two INDEPENDENT sources (census owns
the table; this repository keeps a literal copy); this test is the real check that they agree, and
that the module never reads the export (which would make them agree by construction).
"""

from __future__ import annotations

import ast
from pathlib import Path

from src.core import env_names
from tests.support import legacy_identifiers as li

MODULE = Path(env_names.__file__)


def test_renames_equal_the_export_env_records() -> None:
    export = li.load()
    exported = {(r["old"], r["new"]) for r in export.section("env")}
    ours = {(row.old, row.new) for row in env_names.RENAMES}
    assert (
        ours == exported
    ), f"in RENAMES only: {sorted(ours - exported)}; in the export only: {sorted(exported - ours)}"


def test_retired_matches_status() -> None:
    status = {r["old"]: r["status"] for r in li.load().section("env")}
    for row in env_names.RENAMES:
        assert row.retired == (status[row.old] == "retired"), row.old


#: The siblings' old in-cluster services, which the previous-address lists of ``k8s/base/config.yaml``
#: name until P7 (FR-020.14). The register lists them for this repository from P4 (2026-11-11).
OLD_IN_CLUSTER_HOSTS = {
    "millm-backend.millm.svc.cluster.local": "miserver-backend.miserver.svc.cluster.local",
    "mistudio-backend.mistudio.svc.cluster.local": "miworkbench-backend.miworkbench.svc.cluster.local",
}


def test_export_host_records_are_exactly_the_two_old_in_cluster_services() -> None:
    """miDataworks ALIASES no hostname of its own (FTDD §4.4): the only ``host`` records it carries are
    the two sibling services its previous-address lists must still name, both in transition and both
    observed, so the P7 sampler still reads ``unobserved_hosts`` as empty (CF-24). On the mirror the
    public export withholds them, and this is skipped there by the withheld record's name."""
    export = li.load()
    listed = [li.require_host(export, old) for old in OLD_IN_CLUSTER_HOSTS]
    assert export.section("host") == listed, "a host record beyond the two old in-cluster services"
    assert {r["old"]: r["new"] for r in listed} == OLD_IN_CLUSTER_HOSTS
    assert {(r["status"], r["observed"]) for r in listed} == {("transition", True)}


def test_renames_module_reads_no_file() -> None:
    tree = ast.parse(MODULE.read_text())
    forbidden = {"open", "Path", "json", "tomllib", "importlib", "resources", "read_text"}
    used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not (forbidden & (used | imported)), sorted(forbidden & (used | imported))
    [assign] = [
        n
        for n in tree.body
        if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", None) == "RENAMES"
    ]
    assert isinstance(assign.value, ast.Tuple), "RENAMES is not a literal tuple"
    for element in assign.value.elts:
        assert isinstance(element, ast.Call) and all(
            isinstance(a, ast.Constant) for a in element.args
        ), "a RENAMES row is not built from literals"
