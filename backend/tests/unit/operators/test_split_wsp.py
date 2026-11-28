"""``split@1.3.0``: weight-space programming (WSP) marks (021 FTASKS 4.1 - 4.4; FR-021.6; FTID I3).

Through 003's real executor and the live operator registry (``tests/support/curation_fixtures``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

from tests.fixtures import humor_pool as hp
from tests.support.curation_fixtures import registry, run_operator

ROLES = {"text": "content", "label": "metadata", "body": "content"}
BASE: dict[str, Any] = {
    "split_names": ["fit", "everyday", "hold"],
    "split_fractions": [0.5, 0.25, 0.25],
    "held_out": ["everyday", "hold"],
    "stratify_by": ["label"],
}
WSP: dict[str, Any] = {
    "wsp_roles": ["wsp_practice", "wsp_heldout", "wsp_heldout"],
    "wsp_set_kinds": ["in_distribution", "everyday_text", "long_untruncated"],
    "wsp_min_chars": [0, 0, 5],
    "wsp_text_column": "body",
}


def rows(n: int = 24) -> pa.Table:
    return hp.with_system_columns(
        [
            {
                "text": f"t{i}",
                "body": f"wsp body text number {i} {'x' * (i % 7)}",
                "label": "pos" if i % 2 else "neg",
            }
            for i in range(n)
        ]
    )


def test_split13_is_current_and_every_version_is_served() -> None:
    """FR-021.6: registered AFTER 1.2.0, so it is current (M-12: move the line above 1.2.0)."""
    reg = registry()
    assert reg.current_version("split") == "1.3.0"
    for version in ("1.0.0", "1.1.0", "1.2.0", "1.3.0"):
        assert reg.get("split", version) is not None


@pytest.mark.parametrize("mode", ["reassign", "keep_source"])
def test_split13_golden_equals_12(data_dir: Path, mode: str) -> None:
    """FR-021.6: with no WSP parameter, 1.3.0's table and report equal 1.2.0's (M-05)."""
    table = rows()
    params = dict(BASE, mode=mode)
    if mode == "keep_source":
        names = BASE["split_names"]
        table = table.set_column(
            table.schema.get_field_index("_dw_split"),
            "_dw_split",
            pa.array([names[i % 3] for i in range(table.num_rows)]),
        )
    reports_12: list[dict[str, Any]] = []
    reports_13: list[dict[str, Any]] = []
    old = run_operator("split", params, table, ROLES, version="1.2.0", report_sink=reports_12)
    new = run_operator("split", params, table, ROLES, version="1.3.0", report_sink=reports_13)
    assert old.output.equals(new.output)
    assert reports_12 == reports_13 and len(reports_13) == 1
    assert old.meta.get("split_roles") == new.meta.get("split_roles")
    assert all("wsp" not in r for r in reports_13[0]["split_roles"].values())


def test_split13_reports_each_mark_on_its_split(data_dir: Path) -> None:
    reports: list[dict[str, Any]] = []
    run_operator("split", {**BASE, **WSP}, rows(), ROLES, version="1.3.0", report_sink=reports)
    roles = reports[0]["split_roles"]
    assert roles["fit"]["wsp"] == {
        "role": "wsp_practice",
        "set_kind": "in_distribution",
        "text_column": "body",
        "min_chars": 0,
    }
    assert roles["everyday"]["wsp"]["role"] == "wsp_heldout"
    assert roles["hold"]["wsp"] == {
        "role": "wsp_heldout",
        "set_kind": "long_untruncated",
        "text_column": "body",
        "min_chars": 5,
    }
    assert roles["hold"]["held_out"] is True and roles["fit"]["held_out"] is False


def test_a_split_marked_none_carries_no_mark(data_dir: Path) -> None:
    reports: list[dict[str, Any]] = []
    params = {
        **BASE,
        **WSP,
        "wsp_roles": ["wsp_practice", "none", "wsp_heldout"],
        "wsp_set_kinds": ["in_distribution", "none", "long_untruncated"],
    }
    run_operator("split", params, rows(), ROLES, version="1.3.0", report_sink=reports)
    assert "wsp" not in reports[0]["split_roles"]["everyday"]


#: one case per refusal (FR-021.6): (change, the full detail line the registry wraps)
REFUSALS: dict[str, tuple[dict[str, Any], dict[str, str]]] = {
    "wsp_role_conflicts_held_out": (
        {"wsp_roles": ["wsp_heldout", "wsp_heldout", "wsp_heldout"]},
        {
            "pointer": "/wsp_roles/0",
            "message": "wsp_role_conflicts_held_out: split 'fit' is marked wsp_heldout but is "
            "not in held_out; a WSP held-out partition is a held-out split",
        },
    ),
    "wsp_params_misaligned": (
        {"wsp_min_chars": [0, 5]},
        {
            "pointer": "/wsp_min_chars",
            "message": "wsp_params_misaligned: 2 entries for 3 split names; give one per split, "
            "in the order of split_names (none for a split that is not a WSP partition)",
        },
    ),
    "wsp_set_kind_missing": (
        {"wsp_set_kinds": ["in_distribution", "none", "long_untruncated"]},
        {
            "pointer": "/wsp_set_kinds/1",
            "message": "wsp_set_kind_missing: split 'everyday' has a role and no set kind; give "
            "both or neither",
        },
    ),
    "wsp_min_chars_invalid": (
        {"wsp_min_chars": [0, 0, 0]},
        {
            "pointer": "/wsp_min_chars/2",
            "message": "wsp_min_chars_invalid: split 'hold' is long_untruncated, so it needs a "
            "character floor of at least 1",
        },
    ),
    "wsp_text_column_missing": (
        {"wsp_text_column": None},
        {
            "pointer": "/wsp_text_column",
            "message": "wsp_text_column_missing: a WSP role is declared, so name the text "
            "column whose characters are counted",
        },
    ),
    "wsp_not_in_move": (
        {
            "mode": "move",
            "move_where": "label == pos",
            "move_to": "hold",
            "move_fraction": 0.5,
            "wsp_set_kinds": None,
            "wsp_min_chars": None,
            "wsp_text_column": None,
        },
        {
            "pointer": "/wsp_roles",
            "message": "wsp_not_in_move: mode move declares no split roles, so it cannot mark "
            "WSP partitions; mark them in a reassign or keep_source split step",
        },
    ),
}


@pytest.mark.parametrize("code", sorted(REFUSALS))
def test_split13_refusals(code: str) -> None:
    """Each refusal arrives as its own full detail line inside ``params_invalid`` (R1A-D15),
    never a bare code (M-06 … M-11: drop each check in turn)."""
    change, expected = REFUSALS[code]
    params = {k: v for k, v in {**BASE, **WSP, **change}.items() if v is not None}
    errors = registry().validate_params_detailed("split", "1.3.0", params)
    assert errors == [expected], errors


def test_split13_refuses_in_the_worker_too(data_dir: Path) -> None:
    """ADR-009: the parameters are checked again when the step runs, not only at validation."""
    from src.operators.errors import OperatorError

    params = {**BASE, **WSP}
    params.pop("wsp_text_column")
    with pytest.raises(OperatorError) as caught:
        run_operator("split", params, rows(), ROLES, version="1.3.0")
    assert caught.value.code == "params_invalid"
    assert [e["message"].split(":")[0] for e in caught.value.details["errors"]] == [
        "wsp_text_column_missing"
    ]


def test_a_plain_held_out_split_keeps_either_role_free() -> None:
    """A held-out split may be left unmarked (none), and a training split too."""
    params = {
        **BASE,
        **WSP,
        "wsp_roles": ["none", "none", "wsp_heldout"],
        "wsp_set_kinds": ["none", "none", "long_untruncated"],
    }
    assert registry().validate_params_detailed("split", "1.3.0", params) == []
