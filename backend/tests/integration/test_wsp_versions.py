"""WSP partitions through real builds (021 FTASKS 5.x, 6.x; FR-021.7 - FR-021.12, FR-021.16).

The production operator registry plus the test-only fixture operators (``fx_generator_echo``,
``fx_keep_all``), the real orchestrator and the real finalize. Two fixtures that do not agree by
construction (021-D23): ``wsp-alpha`` (text column ``text``; splits train/test/everyday/long, by
``keep_source`` from its source's own splits) and ``wsp-beta`` (column ``body``; splits fit/hold).
They share no text except the rows a test means to share.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.core.database import sync_session_factory
from src.models import Version
from tests.integration.test_directed_held_out import ECHO, KEEP, carriers, driver, run
from tests.integration.test_version_build import src
from tests.support.operator_build_driver import RealDriver
from tests.support.version_fixtures import make_source

__all__ = ["driver"]

V = "/api/v1/versions"


def alpha_rows(tag: str = "a") -> dict[str, list[dict[str, Any]]]:
    return {
        "train": [
            {"text": f"{tag} practice row {i} about pumps", "label": "neg"} for i in range(6)
        ],
        "test": [{"text": f"{tag} test row {i} about valves", "label": "pos"} for i in range(4)],
        "everyday": [
            {"text": f"{tag} everyday {i}: lunch at noon", "label": "neg"} for i in range(4)
        ],
        "long": [
            {"text": f"{tag} long row {i} " + "word " * (10 + i), "label": "pos" if i else "neg"}
            for i in range(3)
        ],
    }


def split_step(
    names: list[str],
    held: list[str],
    roles: list[str] | None,
    kinds: list[str] | None,
    floors: list[int] | None = None,
    column: str | None = "text",
    *,
    version: str = "1.3.0",
    mode: str = "keep_source",
) -> dict[str, Any]:
    params: dict[str, Any] = {"split_names": names, "held_out": held, "mode": mode}
    if mode == "reassign":
        params["split_fractions"] = [1.0 / len(names)] * len(names)
    if roles is not None:
        params["wsp_roles"] = roles
        params["wsp_set_kinds"] = kinds
        params["wsp_min_chars"] = floors or [0] * len(names)
        if column is not None:
            params["wsp_text_column"] = column
    return {"operator": "split", "version": version, "params": params}


ALPHA_SPLIT = split_step(
    ["train", "test", "everyday", "long"],
    ["test", "everyday", "long"],
    ["wsp_practice", "none", "wsp_heldout", "wsp_heldout"],
    ["in_distribution", "none", "everyday_text", "long_untruncated"],
    [0, 0, 0, 20],
)


def version_row(version_id: str) -> Version:
    with sync_session_factory()() as db:
        row = db.get(Version, version_id)
        assert row is not None
        db.expunge(row)
        return row


def by_name(version_id: str) -> dict[str, dict[str, Any]]:
    return {s["name"]: s for s in version_row(version_id).splits}


async def build(
    client: httpx.AsyncClient,
    driver: RealDriver,
    data_dir: Path,
    rows: dict[str, list[dict[str, Any]]],
    steps: list[dict[str, Any]],
    name: str,
) -> dict[str, Any]:
    source = make_source(data_dir, rows, repo_id=f"t/{name}")
    return await run(client, driver, steps, [src(source)], name)


async def alpha(client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, name: str) -> str:
    out = await build(client, driver, data_dir, alpha_rows(name), [ALPHA_SPLIT], name)
    assert out["status"] == "completed", out
    return str(out["version_id"])


def child_of(version_id: str) -> dict[str, str]:
    return {"kind": "version", "version_id": version_id}


# --- FR-021.7: the mark is recorded, measured and inherited -------------------------------------


async def test_marks_are_recorded_with_the_versions_length(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    version_id = await alpha(client, driver, data_dir, "alpha")
    splits = by_name(version_id)
    assert "wsp" not in splits["test"]
    assert splits["train"]["wsp"]["role"] == "wsp_practice"
    long_mark = splits["long"]["wsp"]
    assert {k: long_mark[k] for k in ("role", "set_kind", "text_column", "min_chars")} == {
        "role": "wsp_heldout",
        "set_kind": "long_untruncated",
        "text_column": "text",
        "min_chars": 20,
    }
    texts = [r["text"] for r in alpha_rows("alpha")["long"]]
    lengths = sorted(len(t) for t in texts)
    assert long_mark["version_length"] == {
        "unit": "characters",
        "min": lengths[0],
        "max": lengths[-1],
        "median": lengths[(len(lengths) - 1) // 2],
    }
    manifest = json.loads(version_row(version_id).manifest)
    assert {s["name"]: s.get("wsp") for s in manifest["splits"]}["long"] == long_mark


async def test_child_inherits_wsp(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """FR-021.7 (M-13: delete the ``wsp`` line in ``inherited_role``)."""
    parent = await alpha(client, driver, data_dir, "parent")
    child = await run(client, driver, [KEEP], [child_of(parent)], "child")
    assert child["status"] == "completed", child
    before, after = by_name(parent), by_name(str(child["version_id"]))
    for name in ("train", "everyday", "long"):
        assert after[name]["wsp"] == before[name]["wsp"], name
    assert "wsp" not in after["test"]


async def test_inputs_that_disagree_on_a_wsp_mark_are_refused(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    one = await alpha(client, driver, data_dir, "one")
    other_step = split_step(
        ["train", "test", "everyday", "long"],
        ["test", "everyday", "long"],
        ["wsp_practice", "none", "wsp_heldout", "wsp_heldout"],
        ["in_distribution", "none", "nonsense_text", "long_untruncated"],
        [0, 0, 0, 20],
    )
    two = await build(client, driver, data_dir, alpha_rows("two"), [other_step], "two")
    assert two["status"] == "completed", two
    both = await run(
        client, driver, [KEEP], [child_of(one), child_of(str(two["version_id"]))], "both"
    )
    assert both["status"] == "failed", both
    assert both["error"]["code"] == "input_split_roles_conflict"
    assert both["error"]["details"]["split"] == "everyday"


async def test_a_non_wsp_build_writes_no_wsp_key(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """FR-021.7: written only when declared, so a version without marks writes today's manifest."""
    plain = split_step(["train", "test", "everyday", "long"], ["test"], None, None)
    out = await build(client, driver, data_dir, alpha_rows("plain"), [plain], "plain")
    assert out["status"] == "completed", out
    assert all("wsp" not in s for s in version_row(str(out["version_id"])).splits)
    manifest = json.loads(version_row(str(out["version_id"])).manifest)
    assert all("wsp" not in s for s in manifest["splits"])


# --- FR-021.8, FR-021.9: floor, rows, text column ------------------------------------------------


def floor_rows(shortest: int) -> dict[str, list[dict[str, Any]]]:
    rows = alpha_rows("floor")
    rows["long"] = [
        {"text": "x" * 30, "label": "pos"},
        {"text": "y" * shortest, "label": "pos"},
    ]
    return rows


@pytest.mark.parametrize(("shortest", "ok"), [(19, False), (20, True)])
async def test_long_floor(
    client: httpx.AsyncClient,
    driver: RealDriver,
    data_dir: Path,
    operator_name: str,
    shortest: int,
    ok: bool,
) -> None:
    """FR-021.8: one character short is refused; exactly at the floor passes (M-14, M-15)."""
    out = await build(client, driver, data_dir, floor_rows(shortest), [ALPHA_SPLIT], "floor")
    if ok:
        assert out["status"] == "completed", out
        assert by_name(str(out["version_id"]))["long"]["wsp"]["version_length"]["min"] == 20
        return
    assert out["status"] == "failed", out
    error = out["error"]
    assert error["code"] == "wsp_long_rows_below_floor"
    assert error["details"]["count"] == 1 and error["details"]["min_chars"] == 20
    assert len(error["details"]["sample_keys"]) == 1
    assert "y" * 19 not in json.dumps(error)  # never row text


async def test_an_empty_wsp_partition_is_refused(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """FR-021.9: a marked split with no rows."""
    rows = alpha_rows("empty")
    del rows["everyday"]
    out = await build(client, driver, data_dir, rows, [ALPHA_SPLIT], "empty")
    assert out["status"] == "failed", out
    assert out["error"]["code"] == "wsp_partition_empty"
    assert out["error"]["details"]["split"] == "everyday"


@pytest.mark.parametrize("column", ["missing_column", "label_id"])
async def test_a_missing_or_non_text_column_is_refused(
    client: httpx.AsyncClient,
    driver: RealDriver,
    data_dir: Path,
    operator_name: str,
    column: str,
) -> None:
    rows = {
        split: [{**r, "label_id": i} for i, r in enumerate(items)]
        for split, items in alpha_rows("col").items()
    }
    step = json.loads(json.dumps(ALPHA_SPLIT))
    step["params"]["wsp_text_column"] = column
    step["params"]["wsp_min_chars"] = [0, 0, 0, 1]
    out = await build(client, driver, data_dir, rows, [step], f"col-{column.replace('_', '-')}")
    assert out["status"] == "failed", out
    assert out["error"]["code"] == "wsp_text_column_invalid"


# --- FR-021.11: within a version ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("into", "refused"),
    [("train", True), ("test", True), ("everyday", False)],
    ids=["practice", "plain_held_out", "two_wsp_heldout_sets"],
)
async def test_heldout_shares_rows(
    client: httpx.AsyncClient,
    driver: RealDriver,
    data_dir: Path,
    operator_name: str,
    into: str,
    refused: bool,
) -> None:
    """FR-021.11 (M-16): a held-out row duplicated in practice or a plain held-out split is
    refused; in two ``wsp_heldout`` sets it passes (R1A-D14)."""
    rows = alpha_rows(f"dup-{into}")
    rows[into].append(dict(rows["long"][0]))
    out = await build(client, driver, data_dir, rows, [ALPHA_SPLIT], f"dup-{into}")
    if not refused:
        assert out["status"] == "completed", out
        return
    assert out["status"] == "failed", out
    error = out["error"]
    assert error["code"] == "wsp_heldout_shares_rows"
    assert error["details"]["splits"] == ["long", into] and error["details"]["count"] == 1


# --- FR-021.12: through lineage -------------------------------------------------------------------


async def test_heldout_rows_left_by_move(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """``split@1.2.0`` mode move declares no roles; moving held-out rows out is refused (M-17)."""
    parent = await alpha(client, driver, data_dir, "move")
    move = {
        "operator": "split",
        "version": "1.2.0",
        "params": {
            "mode": "move",
            "move_where": "label == neg",  # one of the three long rows
            "move_from": ["long"],
            "move_to": "train",
            "move_fraction": 1.0,
        },
    }
    out = await run(client, driver, [move], [child_of(parent)], "move-child")
    assert out["status"] == "failed", out
    error = out["error"]
    assert error["code"] == "wsp_heldout_rows_left_heldout"
    assert error["details"]["split"] == "long" and error["details"]["into"] == "train"
    assert error["details"]["count"] == 1


async def test_heldout_rows_left_by_resplit(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """A re-split that keeps the marks but draws rows anew sends held-out rows to practice."""
    parent = await alpha(client, driver, data_dir, "resplit")
    resplit = dict(ALPHA_SPLIT, params={**ALPHA_SPLIT["params"], "mode": "reassign"})
    resplit["params"]["split_fractions"] = [0.25, 0.25, 0.25, 0.25]
    resplit["params"]["wsp_min_chars"] = [0, 0, 0, 0]
    resplit["params"]["wsp_set_kinds"] = [
        "in_distribution",
        "none",
        "everyday_text",
        "hard_negatives",
    ]
    out = await run(client, driver, [resplit], [child_of(parent)], "resplit-child")
    assert out["status"] == "failed", out
    assert out["error"]["code"] in {"wsp_heldout_redeclared", "wsp_heldout_rows_left_heldout"}


async def test_heldout_redeclared(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """FR-021.12 (M-18): an inherited held-out partition re-declared as another kind is refused,
    though its rows stay where they are (keep_source)."""
    parent = await alpha(client, driver, data_dir, "redeclare")
    again = json.loads(json.dumps(ALPHA_SPLIT))
    again["params"]["wsp_set_kinds"][2] = "nonsense_text"
    out = await run(client, driver, [again], [child_of(parent)], "redeclare-child")
    assert out["status"] == "failed", out
    error = out["error"]
    assert error["code"] == "wsp_heldout_redeclared"
    assert error["details"]["split"] == "everyday"
    assert error["details"]["declared"]["set_kind"] == "nonsense_text"
    same = await run(client, driver, [ALPHA_SPLIT], [child_of(parent)], "same-child")
    assert same["status"] == "completed", same


async def test_heldout_rows_left_by_concatenation(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """A multi-input build whose other input holds the held-out rows in practice is refused."""
    parent = await alpha(client, driver, data_dir, "concat")
    leak = alpha_rows("concat")["long"]
    other = make_source(data_dir, {"train": leak}, repo_id="t/concat-leak")
    out = await run(client, driver, [KEEP], [child_of(parent), src(other)], "concat-child")
    assert out["status"] == "failed", out
    error = out["error"]
    assert error["code"] in {"wsp_heldout_shares_rows", "wsp_heldout_rows_left_heldout"}
    assert error["details"]["count"] == 3


# --- FR-021.16: generated rows -----------------------------------------------------------------------

DIRECTED_WSP = {
    "operator": "split",
    "version": "1.3.0",
    "params": {
        "split_names": ["train", "test", "ood_genre", "ood_generator", "ood_both"],
        "split_fractions": [0.8, 0.2, 0.0, 0.0, 0.0],
        "held_out": ["ood_genre", "ood_generator", "ood_both"],
        "group_columns": ["carrier_id"],
        "directing_columns": ["genre", "generator"],
        "held_out_values": ["g4", "gen-c"],
        "held_out_value_columns": ["genre", "generator"],
        "identity_columns": ["generator"],
        "held_out_split_names": ["ood_genre", "ood_generator", "ood_both"],
    },
}


async def test_wsp_heldout_generated_refused_even_directed(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """P43 (M-39): a directed held-out split holding generated rows under a passing proof builds
    as before WITHOUT a mark, and is refused WITH a ``wsp_heldout`` mark."""
    source = make_source(data_dir, {"train": carriers("w")}, repo_id="t/directed-wsp")
    plain = await run(client, driver, [ECHO, DIRECTED_WSP], [src(source)], "directed-plain")
    assert plain["status"] == "completed", plain
    marked = json.loads(json.dumps(DIRECTED_WSP))
    marked["params"].update(
        {
            "wsp_roles": ["wsp_practice", "none", "wsp_heldout", "none", "none"],
            "wsp_set_kinds": ["in_distribution", "none", "hard_negatives", "none", "none"],
            "wsp_min_chars": [0, 0, 0, 0, 0],
            "wsp_text_column": "text",
        }
    )
    out = await run(client, driver, [ECHO, marked], [src(source)], "directed-wsp")
    assert out["status"] == "failed", out
    assert out["error"]["code"] == "wsp_heldout_contains_generated"
    assert out["error"]["details"]["split"] == "ood_genre"


async def test_a_non_directed_wsp_heldout_with_a_generated_row_is_refused(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """Generated rows added AFTER the split step (an echo keeps its parent's split) reach the WSP
    held-out splits; finalize refuses them by the WSP code, before the plain held-out check."""
    out = await build(client, driver, data_dir, alpha_rows("echo"), [ALPHA_SPLIT, ECHO], "echo-wsp")
    assert out["status"] == "failed", out
    assert out["error"]["code"] == "wsp_heldout_contains_generated"
    assert out["error"]["details"]["split"] == "everyday"


async def test_c3_covers_wsp_heldout(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """FR-021.16 (M-19): near duplicates between practice and held out are C-3's; its held-out list
    includes every ``wsp_heldout`` split because a WSP held-out split is ``held_out: true``."""
    from src.services.publishing.check_inputs import assemble

    version_id = await alpha(client, driver, data_dir, "c3")
    with sync_session_factory()() as db:
        row = db.get(Version, version_id)
        assert row is not None
        inputs = assemble(
            db, row, label_column="label", visibility="private", token_scope="write"  # noqa: S106
        ).inputs
    assert {"everyday", "long"} <= set(inputs.held_out_splits)
    assert "train" not in inputs.held_out_splits


async def test_the_floor_holds_on_every_wsp_split_not_only_long(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """OVN-wsp-dataworks-3 (R1 review fix, OR1-021-7; R12): an in_distribution practice split that
    states min_chars 5 refuses a 4-character row; a manifest never states a floor its rows miss."""
    rows = alpha_rows("ovn3")
    rows["train"].append({"text": "abcd", "label": "neg"})
    step = json.loads(json.dumps(ALPHA_SPLIT))
    step["params"]["wsp_min_chars"] = [5, 0, 0, 20]
    out = await build(client, driver, data_dir, rows, [step], "ovn3")
    assert out["status"] == "failed", out
    assert out["error"]["code"] == "wsp_long_rows_below_floor"
    assert out["error"]["details"]["split"] == "train" and out["error"]["details"]["count"] == 1
