"""Tripwire beside the behaviour tests: every sibling-address comparison goes through
``core/sibling_origins`` (feature 020; FR-020.12; FTID §7.3; FTASKS 6.14).

The behaviour tests (``tests/integration/test_previous_origin_*.py``) are the primary proof. This
file catches a NEW bare comparison added beside them (control M29), and it fails closed: every
named function must be found, or the table is stale (suite memory ``source-scrape-guards-fail-open``).
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
SIBLING_FUNCTIONS = {"matching_values", "same_sibling", "call_base", "prefer_exact"}
#: Field and key names that hold a sibling address (or an endpoint address that may be one).
#: ``millm_base_url`` is miServer's address field (review round 2). The guard's STATED LIMITS (it
#: does not see these; recorded in the 020 P0 record): an address reached through an alias variable
#: or ``getattr``, ``operator.eq``/``__eq__``, an inline ``.rstrip("/") ==`` on a non-address
#: name, ``in``/``match``, SQL ``.is_()``/``.op("=")``/``func.coalesce`` forms, and raw ``text()``.
ADDRESS_NAMES = {"mistudio_base_url", "millm_base_url", "base_url"}

#: Comparisons of an address that are NOT a sibling-identity lookup, each with its reason. Keyed by
#: (module, enclosing function, the expression as ast.unparse prints it). An entry that matches no
#: site is itself a failure (the hatch must stay reachable; memory a-guard-can-work-by-accident).
EXEMPT: dict[tuple[str, str, str], str] = {
    (
        "services/generation/run_service.py",
        "_judge_identity",
        "judge.base_url != resolved.base_url",
    ): (
        "two roles resolved NOW are compared to decide whether to probe a second server; no stored "
        "address is involved"
    ),
    ("services/model_lease_holder.py", "join", "ModelLease.base_url == base_url"): (
        "tracked debt (020 P0 record): a lease row keys on the server origin it was taken on; P2 "
        "requires zero active leases (the GPU-idle gate), so no lease crosses a cutover"
    ),
    ("services/model_lease_holder.py", "rejoin", "ModelLease.base_url == ticket.base_url"): (
        "tracked debt, as above: rejoins the lease this process took, on the origin it took it"
    ),
}

#: (module, function, sibling_origins callee, minimum calls) — FTID §7.3's 13 sites.
SITES: tuple[tuple[str, str, str, int], ...] = (
    ("services/detector_sets/reward_marks.py", "marked_probe_ids", "matching_values", 1),
    ("services/detector_sets/reward_marks.py", "trace", "matching_values", 1),
    ("services/detector_sets/reward_marks.py", "mark", "marked_probe_ids", 1),
    ("workers/detector_send_tasks.py", "do_download", "matching_values", 1),
    ("workers/detector_send_tasks.py", "do_register", "matching_values", 1),
    ("workers/detector_send_tasks.py", "run_job", "call_base", 1),
    ("services/detector_sets/reproduction_links.py", "_existing_link", "matching_values", 1),
    ("services/detector_sets/reproduction_links.py", "create", "_existing_link", 2),
    ("api/v1/endpoints/detector_sets.py", "refresh_probe_results", "call_base", 1),
    ("services/label_run_engine.py", "_score_run", "same_sibling", 1),
    ("services/label_run_engine.py", "_probe_run", "same_sibling", 1),
    ("services/label_run_engine.py", "_feature_run", "same_sibling", 1),
    ("services/generation/run_engine.py", "_run", "same_sibling", 1),
    # ENH-006 G21 (found by the 2026-11-08 review, after the FTID's table was verified).
    ("services/detector_sets/set_service.py", "sibling_views", "same_sibling", 1),
)


def _functions(tree: ast.AST) -> dict[str, ast.AST]:
    found: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            found.setdefault(node.name, node)
    return found


def _callee(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _address(node: ast.AST) -> bool:
    if isinstance(node, ast.Attribute) and node.attr in ADDRESS_NAMES:
        return True
    if (
        isinstance(node, ast.Call)
        and _callee(node) == "get"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value in ADDRESS_NAMES
    ):
        return True
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.slice, ast.Constant)
        and node.slice.value in ADDRESS_NAMES
    )


def _covered(tree: ast.AST) -> set[int]:
    """ids of nodes that go through sibling_origins: arguments of one of its calls, or operands of
    an ``or`` whose other side calls one (``_same_address(a, b) or same_sibling(S, a, b)``)."""
    covered: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _callee(node) in SIBLING_FUNCTIONS:
            for arg in [*node.args, *(k.value for k in node.keywords)]:
                covered.update(id(n) for n in ast.walk(arg))
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            # Review round 2: only the operands that compare the SAME addresses the sibling call is
            # given. `x.base_url == y or same_sibling(S, a, b)` routes nothing through
            # sibling_origins; it used to be covered whole.
            routed = {
                ast.unparse(arg)
                for n in ast.walk(node)
                if isinstance(n, ast.Call) and _callee(n) in SIBLING_FUNCTIONS
                for arg in [*n.args, *(k.value for k in n.keywords)]
            }
            for operand in node.values:
                compared = [n for n in ast.walk(operand) if _address(n)]
                compared += [
                    a
                    for n in ast.walk(operand)
                    if isinstance(n, ast.Call) and _callee(n) == "_same_address"
                    for a in n.args
                ]
                if compared and all(ast.unparse(n) in routed for n in compared):
                    covered.update(id(n) for n in ast.walk(operand))
    return covered


def _enclosing(tree: ast.AST) -> dict[int, str]:
    owner: dict[int, str] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            for n in ast.walk(fn):
                owner[id(n)] = fn.name  # the innermost function wins (walked later)
    return owner


def bare_compares(source: str) -> list[tuple[int, str, str]]:
    """(line, enclosing function, expression) of every address comparison not routed through
    sibling_origins: ``==``/``!=`` with an address operand, a ``_same_address(...)`` call, an
    address column's ``.in_(...)`` not fed by ``matching_values``, and ``filter_by(<address>=...)``.
    """
    tree = ast.parse(source)
    covered = _covered(tree)
    owner = _enclosing(tree)
    found: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if id(node) in covered:
            continue
        hit = False
        if isinstance(node, ast.Compare) and any(
            isinstance(op, ast.Eq | ast.NotEq) for op in node.ops
        ):
            hit = any(_address(o) for o in [node.left, *node.comparators])
        elif isinstance(node, ast.Call):
            name = _callee(node)
            if name == "_same_address":
                hit = True
            elif name == "in_" and isinstance(node.func, ast.Attribute):
                fed = (
                    node.args
                    and isinstance(node.args[0], ast.Call)
                    and _callee(node.args[0]) == "matching_values"
                )
                hit = _address(node.func.value) and not fed
            elif name == "filter_by":
                hit = any(k.arg in ADDRESS_NAMES for k in node.keywords)
        if hit:
            found.append((node.lineno, owner.get(id(node), "<module>"), ast.unparse(node)))
    return found


def _scanned_files() -> list[Path]:
    files = sorted(SRC.rglob("*.py"))
    assert len(files) > 300, "backend/src was not read"
    return files


def _all_sites() -> dict[str, list[tuple[int, str, str]]]:
    out = {}
    for path in _scanned_files():
        sites = bare_compares(path.read_text())
        if sites:
            out[str(path.relative_to(SRC))] = sites
    return out


def offenders(
    sites_by_module: dict[str, list[tuple[int, str, str]]],
) -> dict[str, list[tuple[int, str, str]]]:
    """The sites that carry no exemption: an exemption matches its exact (module, function,
    expression) key, never a whole function or module."""
    found = {
        module: [s for s in sites if (module, s[1], s[2]) not in EXEMPT]
        for module, sites in sites_by_module.items()
    }
    return {m: s for m, s in found.items() if s}


def test_no_bare_address_compare_in_the_backend() -> None:
    """The WHOLE backend package (derived, not a module list): every address comparison goes through
    core.sibling_origins or carries a reasoned exemption."""
    bad = offenders(_all_sites())
    assert bad == {}, f"compare sibling addresses through core.sibling_origins: {bad}"


def test_an_exemption_covers_only_its_own_expression() -> None:
    """Review round 2 (survivor T01): a NEW bare comparison inside an exempt function is still an
    offender; the exemption names one expression, not the function around it."""
    for module, function, expression in EXEMPT:
        planted = {module: [(1, function, expression), (2, function, "row.base_url == here")]}
        assert offenders(planted) == {module: [(2, function, "row.base_url == here")]}


def test_the_scan_reads_every_module_it_guards() -> None:
    """Review round 2 (survivor T05): the 300-file floor proves a walk happened, not that the
    modules holding the sites were in it. Every module named by SITES or EXEMPT is scanned, and
    every scanned file parsed (a syntax or decoding error raises; it is never skipped)."""
    scanned = {str(p.relative_to(SRC)) for p in _scanned_files()}
    named = {module for module, *_ in SITES} | {module for module, *_ in EXEMPT}
    assert named <= scanned, sorted(named - scanned)


def test_every_exemption_still_matches_a_site() -> None:
    found = {(m, fn, expr) for m, sites in _all_sites().items() for _, fn, expr in sites}
    stale = sorted(set(EXEMPT) - found)
    assert stale == [], f"exemptions that match nothing (stale or mistyped): {stale}"


def test_each_site_calls_its_sibling_origins_function() -> None:
    for module, function, callee, minimum in SITES:
        tree = ast.parse((SRC / module).read_text())
        node = _functions(tree).get(function)
        assert node is not None, f"{module}: {function} not found (the site table is stale)"
        calls = [n for n in ast.walk(node) if isinstance(n, ast.Call) and _callee(n) == callee]
        assert len(calls) >= minimum, f"{module}:{function} calls {callee} {len(calls)}x"


def test_the_tripwire_finds_every_named_function() -> None:
    """Fail closed: each listed function exists, and the scanner sees each form it claims to see."""
    for module, function, *_ in SITES:
        assert function in _functions(ast.parse((SRC / module).read_text())), (module, function)
    planted = (
        "def a(send, snap, url, s):\n"
        "    x = send.mistudio_base_url == url\n"
        "    y = url != snap.get('base_url')\n"
        "    z = snap['base_url'] == url\n"
        "    w = _same_address(send.mistudio_base_url, url)\n"
        "    q = Reg.mistudio_base_url.in_([url])\n"
        "    r = s.query(Reg).filter_by(mistudio_base_url=url)\n"
        "    ok1 = same_sibling(S, url, snap.get('base_url'))\n"
        "    ok2 = _same_address(send.mistudio_base_url, url) or same_sibling(S, send.mistudio_base_url, url)\n"
        "    ok3 = Reg.mistudio_base_url.in_(matching_values(s, Reg.mistudio_base_url, S, url))\n"
        "    bad1 = send.mistudio_base_url == url or flag\n"
        "    bad2 = send.mistudio_base_url == url or same_sibling(S, a, b)\n"
        "    bad3 = _same_address(send.base_url, url) or same_sibling(S, send.mistudio_base_url, url)\n"
        "    bad4 = settings.millm_base_url != stored\n"
    )
    assert sorted(line for line, _, _ in bare_compares(planted)) == [
        2,
        3,
        4,
        5,
        6,
        7,
        11,
        12,
        13,
        14,
    ]
