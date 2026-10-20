"""Seed candidates, seed selection and seed-row checks, shared by the run worker and the preview.

One implementation, two callers: ``run_engine.GenerationEngine._seeds`` (the run) and
``run_service.preview`` (a preview drawn from real seed rows). A preview that chose or rendered
rows differently from the run would show the operator something the run never sends.

Seeds are chosen by ROW KEY: rows that share a key (copies) collapse to one seed, the copy with the
lowest ``_dw_occurrence``. :func:`copies_disagree` measures when that choice is visible — when a
column the templates read differs between copies of one key — so the plan can say so.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .. import label_inputs
from ..duck import connect, files_param, quote_ident, top_level_columns
from . import rules

PROMPT_PLACEHOLDER = "prompt"


def template_placeholders(bodies: Iterable[Mapping[str, Any] | None]) -> list[str]:
    """Every placeholder other than ``{prompt}`` that the template bodies' prompt or system use."""
    names: list[str] = []
    for body in bodies:
        if body is None:
            continue
        for text in (body.get("prompt"), body.get("system")):
            for name in rules.placeholders(str(text or "")):
                if name != PROMPT_PLACEHOLDER and name not in names:
                    names.append(name)
    return names


def needed_columns(prompt_column: str, bodies: Iterable[Mapping[str, Any] | None]) -> list[str]:
    """The columns a seed row must carry: the prompt column and every template placeholder."""
    return sorted({prompt_column, *template_placeholders(bodies)})


def seed_candidates(
    splits: Sequence[Mapping[str, Any]], seed_splits: Sequence[str], needed: Sequence[str]
) -> list[dict[str, Any]]:
    """Source rows of the seed splits, one per key (FR-007.36): the copy with the lowest
    ``_dw_occurrence``. The caller re-checks every row this returns before using it."""
    files = label_inputs.version_files(list(splits))
    con = connect()
    try:
        columns = top_level_columns(con, files)
        key = quote_ident("_dw_row_key", columns)
        origin = quote_ident("_dw_origin", columns)
        split = quote_ident("_dw_split", columns)
        extra = "".join(f", {quote_ident(c, columns)}" for c in needed)
        marks = ", ".join("?" for _ in seed_splits)
        sql = (
            f"SELECT {key} AS row_key, {split} AS split, {origin} AS origin{extra} "  # noqa: S608 - quoted
            f"FROM read_parquet(?) WHERE {origin} = 'source' AND {split} IN ({marks}) "
            f'QUALIFY row_number() OVER (PARTITION BY {key} ORDER BY "_dw_occurrence") = 1'
        )
        rows: list[dict[str, Any]] = (
            con.execute(sql, [files_param(files), *seed_splits]).to_arrow_table().to_pylist()
        )
        return rows
    finally:
        con.close()


def select_seed_rows(
    candidates: Sequence[Mapping[str, Any]],
    needed: Sequence[str],
    prompt_column: str,
    sample_size: int,
    seed: int,
) -> list[dict[str, Any]]:
    """``sample_size`` seeds drawn by key with ``rules.select_seeds``, in drawn order, each with
    the values of every needed column (the shape the run stores in its seeds file)."""
    by_key = {str(c["row_key"]): c for c in candidates}
    chosen = rules.select_seeds(list(by_key), sample_size, int(seed))
    rows = []
    for position, key in enumerate(chosen):
        c = by_key[key]
        values = {k: c.get(k) for k in needed}
        rows.append(
            {
                "position": position,
                "row_key": key,
                "split": c["split"],
                "origin": c["origin"],
                "prompt": None if c.get(prompt_column) is None else str(c[prompt_column]),
                "values": json.dumps(values, sort_keys=True, ensure_ascii=False, default=str),
            }
        )
    return rows


def check_seed_rows(
    rows: Sequence[Mapping[str, Any]], seed_splits: Sequence[str], held_out: Sequence[str]
) -> None:
    """The per-row re-check (FR-007.36): every seed is a SOURCE row of a seed split and never in
    a held-out split, whatever the query or a stored file says. Raises ``GenerationRuleError``."""
    for row in rows:
        if row["split"] in set(held_out) or not rules.row_is_eligible_seed(
            row["origin"], row["split"], seed_splits
        ):
            raise rules.GenerationRuleError(
                "HELD_OUT_SEED" if row["split"] in set(held_out) else "SEED_NOT_ELIGIBLE",
                f"Seed row {str(row['row_key'])[:12]} is in split {row['split']!r} with origin "
                f"{row['origin']!r}; seeds must be source rows of {list(seed_splits)}, never "
                "held out. Nothing was generated from it.",
            )


def render_messages(body: Mapping[str, Any], values: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A template body's chat messages for one seed: ``values`` are the seed row's columns and
    ``prompt``. The run and the preview both call this, so a preview sends what a run sends."""
    messages: list[dict[str, Any]] = []
    system = body.get("system")
    if system:
        messages.append({"role": "system", "content": rules.render_template(str(system), values)})
    messages.append({"role": "user", "content": rules.render_template(str(body["prompt"]), values)})
    return messages


def copies_disagree(
    splits: Sequence[Mapping[str, Any]], seed_splits: Sequence[str], columns: Sequence[str]
) -> dict[str, Any]:
    """Among source rows of the seed splits: how many rows, how many keys, how many keys have
    copies, and per column in ``columns`` how many keys' copies disagree (a NULL beside a value
    counts as disagreeing). ``keys_affected`` is the number of keys where ANY column disagrees."""
    files = label_inputs.version_files(list(splits))
    con = connect()
    try:
        present = top_level_columns(con, files)
        key = quote_ident("_dw_row_key", present)
        origin = quote_ident("_dw_origin", present)
        split = quote_ident("_dw_split", present)
        wanted = [c for c in columns if c in present]
        marks = ", ".join("?" for _ in seed_splits)
        per_col = [
            f"(count(DISTINCT {quote_ident(c, present)}) > 1 OR "
            f"(count({quote_ident(c, present)}) > 0 AND "
            f"count({quote_ident(c, present)}) < count(*))) AS d{i}"
            for i, c in enumerate(wanted)
        ]
        inner = (
            f"SELECT {key} AS k, count(*) AS n"  # noqa: S608 - quoted
            + "".join(f", {p}" for p in per_col)
            + f" FROM read_parquet(?) WHERE {origin} = 'source' AND {split} IN ({marks}) "
            f"GROUP BY {key}"
        )
        any_col = " OR ".join(f"d{i}" for i in range(len(wanted))) or "false"
        outer = (
            "SELECT coalesce(sum(n), 0), count(*), count(*) FILTER (WHERE n > 1), "  # noqa: S608 - quoted
            f"count(*) FILTER (WHERE n > 1 AND ({any_col}))"
            + "".join(f", count(*) FILTER (WHERE n > 1 AND d{i})" for i in range(len(wanted)))
            + f" FROM ({inner})"
        )
        row = con.execute(outer, [files_param(files), *seed_splits]).fetchone()
        assert row is not None
        by_column = {c: int(row[4 + i]) for i, c in enumerate(wanted) if int(row[4 + i]) > 0}
        return {
            "rows": int(row[0]),
            "keys": int(row[1]),
            "keys_with_copies": int(row[2]),
            "keys_affected": int(row[3]),
            "columns": by_column,
        }
    finally:
        con.close()
