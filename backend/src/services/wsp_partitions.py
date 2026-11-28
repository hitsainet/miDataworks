"""Weight-space programming (WSP) partitions: declarations, finalize checks, shipped marks and the
partitions answer (021 FR-021.6 - FR-021.16, FR-021.26; FTDD §6.2; FTID I3, I5, I7).

A WSP mark says what a split is FOR when a caller fits weights: ``wsp_practice`` (it may fit on
these rows) or ``wsp_heldout`` (it may only evaluate on them). The rule this module guards: a
held-out partition is never offered as practice, within a version, through lineage, or at a build.
Disjointness is EXACT, by ``_dw_row_key`` (the content hash, ``services/row_keys.py``); near
duplicates are check C-3's. Every refusal carries split names, counts and at most 20 row keys,
never row text (FTID §12).

This module is the only producer of a manifest's ``wsp`` (:func:`shipped_wsp`) and of the WSP
checks finalize runs (:func:`check_finalize`); finalize, the two build sites and the route call
it (021-D22).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

WSP_ROLES = ("wsp_practice", "wsp_heldout")
WSP_SET_KINDS = (
    "in_distribution",
    "hard_negatives",
    "everyday_text",
    "nonsense_text",
    "long_untruncated",
    "activation_corpus",
)
NONE = "none"
#: ``split@1.3.0``'s parameters (FR-021.6): three arrays parallel to ``split_names`` and a column.
WSP_PARAMS = ("wsp_roles", "wsp_set_kinds", "wsp_min_chars", "wsp_text_column")
#: The keys of a stored mark that a manifest copies, key by key (R2B-10); ``length`` is measured.
MARK_KEYS = ("role", "set_kind", "text_column", "min_chars")
MAX_KEYS = 20


def _problem(pointer: str, code: str, message: str) -> str:
    """The registry's ``check_params`` line: ``<pointer>: <code>: <message>`` (R1A-D15)."""
    return f"{pointer}: {code}: {message}"


def check_params(params: Mapping[str, Any]) -> list[str]:
    """The six WSP parameter refusals of ``split@1.3.0`` (FR-021.6), pure; [] when none apply.

    Runs at validation and again in the worker (ADR-009). The order is fixed: a mode that cannot
    declare roles first, then shape (lengths), then each split's own declaration."""
    given = [k for k in WSP_PARAMS if params.get(k) is not None]
    if not given:
        return []
    if params.get("mode") == "move":
        return [
            _problem(
                f"/{k}",
                "wsp_not_in_move",
                "mode move declares no split roles, so it cannot mark WSP partitions; mark them "
                "in a reassign or keep_source split step",
            )
            for k in given
        ]
    names = [str(n) for n in (params.get("split_names") or ["train", "test"])]
    held = {str(h) for h in (params.get("held_out") or ["test"])}
    arrays = {k: params.get(k) for k in ("wsp_roles", "wsp_set_kinds", "wsp_min_chars")}
    problems = [
        _problem(
            f"/{k}",
            "wsp_params_misaligned",
            f"{len(v)} entries for {len(names)} split names; give one per split, in the order "
            "of split_names (none for a split that is not a WSP partition)",
        )
        for k, v in arrays.items()
        if v is not None and len(v) != len(names)
    ]
    if problems:
        return problems
    roles = list(arrays["wsp_roles"] or [NONE] * len(names))
    kinds = list(arrays["wsp_set_kinds"] or [NONE] * len(names))
    floors = list(arrays["wsp_min_chars"] or [0] * len(names))
    for i, name in enumerate(names):
        role, kind, floor = str(roles[i]), str(kinds[i]), int(floors[i])
        if role == "wsp_heldout" and name not in held:
            problems.append(
                _problem(
                    f"/wsp_roles/{i}",
                    "wsp_role_conflicts_held_out",
                    f"split {name!r} is marked wsp_heldout but is not in held_out; a WSP "
                    "held-out partition is a held-out split",
                )
            )
        if role == "wsp_practice" and name in held:
            problems.append(
                _problem(
                    f"/wsp_roles/{i}",
                    "wsp_role_conflicts_held_out",
                    f"split {name!r} is marked wsp_practice but is in held_out; a practice "
                    "partition is a training split",
                )
            )
        if (role == NONE) != (kind == NONE):
            problems.append(
                _problem(
                    f"/wsp_set_kinds/{i}",
                    "wsp_set_kind_missing",
                    f"split {name!r} has "
                    + ("a role and no set kind" if kind == NONE else "a set kind and no role")
                    + "; give both or neither",
                )
            )
        if kind == "long_untruncated" and floor < 1:
            problems.append(
                _problem(
                    f"/wsp_min_chars/{i}",
                    "wsp_min_chars_invalid",
                    f"split {name!r} is long_untruncated, so it needs a character floor of at "
                    "least 1",
                )
            )
    if any(str(r) != NONE for r in roles) and not str(params.get("wsp_text_column") or ""):
        problems.append(
            _problem(
                "/wsp_text_column",
                "wsp_text_column_missing",
                "a WSP role is declared, so name the text column whose characters are counted",
            )
        )
    return problems


def declared_from_params(params: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """``{split: {role, set_kind, text_column, min_chars}}`` for each split a step marks; a split
    marked ``none`` (or every split, when no WSP parameter is given) is absent."""
    roles = params.get("wsp_roles")
    if not roles:
        return {}
    names = [str(n) for n in (params.get("split_names") or ["train", "test"])]
    kinds = list(params.get("wsp_set_kinds") or [NONE] * len(names))
    floors = list(params.get("wsp_min_chars") or [0] * len(names))
    column = str(params.get("wsp_text_column") or "")
    return {
        name: {
            "role": str(roles[i]),
            "set_kind": str(kinds[i]),
            "text_column": column,
            "min_chars": int(floors[i]),
        }
        for i, name in enumerate(names)
        if str(roles[i]) != NONE
    }


def declared(split_roles: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """The marks in a step's (or finalize's) ``split_roles``: ``{split: mark}``."""
    return {name: dict(role["wsp"]) for name, role in split_roles.items() if role.get("wsp")}


def declared_on(splits: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """The marks a version's stored split entries carry: ``{split: mark}``."""
    return {str(s["name"]): dict(s["wsp"]) for s in splits if s.get("wsp")}


# --- before a build: check (6) as validation can see it (MCP gap G-c, 2026-11-25) --------------

#: Finalize's check (6) code; validation reports the same code before a build.
GENERATED_IN_HELDOUT = "wsp_heldout_contains_generated"
#: The suite rule it enforces.
GENERATED_IN_HELDOUT_RULE = "P43"


def inputs_generated(splits: Sequence[tuple[str, str, Path]]) -> list[dict[str, Any]]:
    """``[{version_id, split, sample_keys}]`` for each input split holding a generated row.

    ``splits`` is ``(version_id, split, file)``. The test is finalize's own,
    :func:`version_finalize.held_out_violations` (check 6), read over the input's files: a row whose
    ``_dw_origin`` is ``generated``."""
    from .version_finalize import held_out_violations

    found = []
    for version_id, split, path in splits:
        keys = held_out_violations(path)
        if keys:
            found.append({"version_id": version_id, "split": split, "sample_keys": keys})
    return found


#: A finding's severity: ``error`` where finalize's check (6) refuses for certain, ``warning`` where
#: generated rows only may land in the marked split (operator decision, 2026-11-25).
Severity = Literal["error", "warning"]


def generated_heldout_problems(
    steps: Sequence[tuple[int, str, Mapping[str, Any]]],
    inputs: Sequence[Mapping[str, Any]] | None,
) -> list[tuple[int, Severity, str]]:
    """``[(step index, severity, message)]`` for each step that lets generated rows reach a
    ``wsp_heldout`` split, so finalize's check (6) would refuse the build (P43).

    ``steps`` is ``(index, kind, params)`` in recipe order; ``inputs`` is :func:`inputs_generated`
    over the build's inputs, or ``None`` when they are not known (a recipe validated alone).

    Generated rows come from the inputs or from a step whose kind may add rows (the kind effect
    table, ``operators/effects.py``: a generator). The severity follows the operator's decision of
    2026-11-25:

    - **error**, certain: an input version that already holds generated rows reaching a marked
      split (a split step that reassigns rows, or ``keep_source`` keeping an input's split of the
      marked name); or a step that adds rows AFTER a split was marked (an added row can keep its
      parent's split: ``fx_generator_echo``, ``slot_siblings``);
    - **warning**, possible: a generator BEFORE a split that reassigns rows. Whether any generated
      row lands in the marked split depends on the split's own assignment (a value-directed split
      places a row by its values), so finalize refuses only if one does.

    ``keep_source`` keeps each row's split, so rows a generator added before it (they keep their
    seed's split, which ``dw_generated_rows`` never takes from a held-out split) are not reported.
    """
    from ..operators.effects import ALLOWED

    known = {str(i["split"]) for i in inputs or ()}
    from_inputs = [f"input version {i['version_id']} split {i['split']!r}" for i in inputs or ()]
    from_steps: list[str] = []
    #: generated rows may sit in any split (after a reassigning split): certain when inputs held
    #: them, possible when only a generator did.
    anywhere: Severity | None = None
    marked: list[str] = []
    problems: list[tuple[int, Severity, str]] = []
    rule = (
        "Finalize refuses a generated row in a WSP held-out split, whatever its direction or "
        "proof (P43, 021-D26). Leave the split unmarked (a value-directed held-out split is still "
        "guarded by its proof), or keep generated rows out of it."
    )
    for index, kind, params in steps:
        effects = ALLOWED.get(kind, frozenset())
        keep = params.get("mode") == "keep_source"
        held = sorted(
            n for n, m in declared_from_params(params).items() if m["role"] == "wsp_heldout"
        )
        if held and (from_inputs or from_steps):
            severity: Severity | None
            if keep:
                in_known = sorted(set(held) & known)
                reached = in_known or (held if anywhere else [])
                severity = "error" if in_known else anywhere
            else:
                reached = held
                severity = "error" if from_inputs else "warning"
            if reached and severity is not None:
                names = ", ".join(repr(n) for n in reached)
                sources = from_inputs if severity == "error" else from_inputs + from_steps
                it = "it" if len(reached) == 1 else "them"
                if severity == "error":
                    text = (
                        f"Step {index} marks {names} wsp_heldout, and generated rows reach {it} "
                        f"(from {'; '.join(sources)}). {rule}"
                    )
                else:
                    text = (
                        f"Step {index} marks {names} wsp_heldout, and generated rows from "
                        f"{'; '.join(sources)} may be assigned to {it}. If any generated row lands "
                        f"there, finalize refuses the build with {GENERATED_IN_HELDOUT} (P43, "
                        "021-D26). Leave the split unmarked, or keep generated rows out of it."
                    )
                problems.append((index, severity, text))
        if "add_rows" in effects:
            if marked:
                names = ", ".join(repr(n) for n in marked)
                problems.append(
                    (
                        index,
                        "error",
                        f"Step {index}, a {kind}, adds rows after {names} "
                        + ("was" if len(marked) == 1 else "were")
                        + " marked wsp_heldout, and an added row can keep its parent's split. "
                        + rule,
                    )
                )
            from_steps.append(f"step {index}, a {kind}, which adds rows")
        if "assign_split" in effects and not keep:
            if from_inputs:
                anywhere = "error"
            elif from_steps and anywhere is None:
                anywhere = "warning"
        marked += [n for n in held if n not in marked]
    return problems


# --- finalize (FR-021.7 - FR-021.9, FR-021.11, FR-021.12, FR-021.16; FTID I4, I5) ---------------


def mark_of(entry: Mapping[str, Any]) -> dict[str, Any] | None:
    """A split entry's or a role's mark, the four declared keys only (a stored entry also carries
    ``version_length``, which is the version's measurement, not part of the declaration)."""
    wsp = entry.get("wsp")
    if not wsp:
        return None
    return {k: wsp[k] for k in MARK_KEYS}


def _refuse(message: str, code: str, details: dict[str, Any]) -> Exception:
    """A finalize refusal; its next step is the recipe, where the split step is declared."""
    from ..rules.next_step import next_for
    from .assembly import BuildRefusal

    return BuildRefusal(
        message,
        code=code,
        details={
            **details,
            "next": next_for("dataworks_revise_recipe", why="change the split step, then build"),
        },
    )


def measure(path: Path, text_column: str) -> tuple[dict[str, Any], Any]:
    """``(length, lengths)``: the character lengths of ``text_column`` in one Parquet file, in
    Unicode code points (``pc.utf8_length``, R1A-D21; a null counts 0), and the per-row array.
    ``median`` is the lower median."""
    import pyarrow.compute as pc

    column = _text_values(path, text_column)
    if column is None:
        raise KeyError(text_column)
    lengths = pc.fill_null(pc.utf8_length(column), 0)
    if len(lengths) == 0:
        return {"unit": "characters", "min": 0, "max": 0, "median": 0}, lengths
    span = pc.min_max(lengths).as_py()
    median = pc.quantile(lengths, q=0.5, interpolation="lower").to_pylist()[0]
    return {
        "unit": "characters",
        "min": int(span["min"]),
        "max": int(span["max"]),
        "median": int(median),
    }, lengths


def _text_values(path: Path, column: str) -> Any:
    """One column of a split file (Parquet, or an export's JSON Lines), or None when absent."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    if path.suffix == ".jsonl":
        import pyarrow.json as pj

        if path.stat().st_size == 0:
            return pa.chunked_array([], pa.string())
        table = pj.read_json(path)
        return table.column(column) if column in table.column_names else None
    if column not in pq.read_schema(path).names:
        return None
    return pq.read_table(path, columns=[column]).column(0)


def _text_column_problem(path: Path, column: str) -> str | None:
    import pyarrow.parquet as pq

    from .duck import holds_text

    schema = pq.read_schema(path)
    if column not in schema.names:
        return f"has no column {column!r}"
    if not holds_text(schema.field(column).type):
        return f"column {column!r} holds {schema.field(column).type}, not text"
    return None


def _shared_keys(held: Path, others: Sequence[Path]) -> tuple[int, list[str]]:
    """Exact duplicates: the count of distinct row keys of ``held`` found in ``others``, and at most
    20 of them. Reads ``_dw_row_key`` only; never loads a key column into Python."""
    from .duck import connect, files_param

    if not others:
        return 0, []
    con = connect()
    try:
        shared = (
            "SELECT DISTINCT h._dw_row_key AS k FROM read_parquet(?) h "
            "JOIN (SELECT _dw_row_key FROM read_parquet(?)) o USING (_dw_row_key)"
        )
        args = [files_param([held]), files_param(list(others))]
        row = con.execute(f"SELECT count(*) FROM ({shared})", args).fetchone()  # noqa: S608
        count = int(row[0]) if row else 0
        keys = (
            [
                str(r[0])
                for r in con.execute(
                    f"SELECT k FROM ({shared}) ORDER BY k LIMIT {MAX_KEYS}", args  # noqa: S608
                ).fetchall()
            ]
            if count
            else []
        )
    finally:
        con.close()
    return count, keys


def _added_rows(written: Path, inherited: Sequence[Path]) -> tuple[int, list[str]]:
    """Rows of ``written`` beyond the inherited files' rows, by row key and count (a second copy
    of an inherited row is an added row too), and at most 20 of their keys. DuckDB only."""
    from .duck import connect, files_param

    con = connect()
    try:
        added = (
            "WITH w AS (SELECT _dw_row_key AS k, count(*) AS n FROM read_parquet(?) GROUP BY 1), "
            "i AS (SELECT _dw_row_key AS k, count(*) AS n FROM read_parquet(?) GROUP BY 1) "
            "SELECT w.k AS k, w.n - coalesce(i.n, 0) AS extra FROM w LEFT JOIN i USING (k) "
            "WHERE w.n > coalesce(i.n, 0)"
        )
        args = [files_param([written]), files_param(list(inherited))]
        row = con.execute(f"SELECT sum(extra) FROM ({added})", args).fetchone()  # noqa: S608
        count = int(row[0]) if row and row[0] is not None else 0
        keys = (
            [
                str(r[0])
                for r in con.execute(
                    f"SELECT k FROM ({added}) ORDER BY k LIMIT {MAX_KEYS}", args  # noqa: S608
                ).fetchall()
            ]
            if count
            else []
        )
    finally:
        con.close()
    return count, keys


def input_heldout(session: Any, version_ids: Sequence[str]) -> list[dict[str, Any]]:
    """Every ``wsp_heldout`` split of the build's version inputs: ``{version_id, split, path,
    mark}``, in request order."""
    from ..core.storage import resolve_under_data_dir
    from ..models.version import Version

    out: list[dict[str, Any]] = []
    for version_id in version_ids:
        row = session.get(Version, version_id)
        if row is None:
            continue
        for split in row.splits:
            mark = mark_of(split)
            if mark is not None and mark["role"] == "wsp_heldout":
                out.append(
                    {
                        "version_id": version_id,
                        "split": split["name"],
                        "path": resolve_under_data_dir(split["path"]),
                        "mark": mark,
                    }
                )
    return out


def check_finalize(
    session: Any,
    written: Sequence[tuple[str, Path]],
    split_roles: Mapping[str, Mapping[str, Any]],
    version_input_ids: Sequence[str],
    *,
    roles_declared_by_step: bool,
) -> dict[str, dict[str, Any]]:
    """The WSP checks of a build, over its WRITTEN files, before anything is committed (FTDD §6.2).

    In order: (1) the text column exists and holds text; (2) no WSP split is empty; (3) every
    row meets its split's character floor; (4) a ``wsp_heldout`` split shares no row with a split
    of this version that is not ``wsp_heldout``; (5) lineage: no ``wsp_heldout`` row of an input
    reaches a split that is not ``wsp_heldout``, and an inherited ``wsp_heldout`` split is not
    re-declared as anything else; (6) a ``wsp_heldout`` split holds no generated row, directed or
    not (P43); (7) an inherited ``wsp_heldout`` split gains no row (``wsp_heldout_grew``, operator
    decision 2026-11-25: it keeps exactly its rows, and a larger held-out set is a new split).
    Returns ``{split: version_length}`` for each marked split. Raises BuildRefusal."""
    from .version_finalize import held_out_violations

    marks = {n: m for n, r in split_roles.items() if (m := mark_of(r)) is not None}
    paths = dict(written)
    lengths: dict[str, dict[str, Any]] = {}
    heldout = {n for n, m in marks.items() if m["role"] == "wsp_heldout"}
    for name, mark in marks.items():
        if name not in paths:  # (2): a marked split the build wrote no file for has no rows
            raise _refuse(
                f"WSP split {name!r} has no rows. A {mark['role']} partition must hold rows; "
                "mark another split or change the split fractions.",
                "wsp_partition_empty",
                {"split": name},
            )
        path = paths[name]
        problem = _text_column_problem(path, mark["text_column"])
        if problem is not None:  # (1)
            raise _refuse(
                f"WSP split {name!r} {problem}. Name a text column the split's rows carry in "
                "wsp_text_column.",
                "wsp_text_column_invalid",
                {"split": name, "text_column": mark["text_column"]},
            )
        length, per_row = measure(path, mark["text_column"])
        if len(per_row) == 0:  # (2)
            raise _refuse(
                f"WSP split {name!r} has no rows. A {mark['role']} partition must hold rows; "
                "mark another split or change the split fractions.",
                "wsp_partition_empty",
                {"split": name},
            )
        floor = int(mark["min_chars"])
        if floor > 0:  # (3)
            import pyarrow.compute as pc
            import pyarrow.parquet as pq

            short_mask = pc.less(per_row, floor)
            short = int(pc.sum(short_mask).as_py() or 0)
            if short:
                keys = pq.read_table(path, columns=["_dw_row_key"]).column(0)
                sample = pc.filter(keys, short_mask).slice(0, MAX_KEYS).to_pylist()
                raise _refuse(
                    f"{short} row(s) of WSP split {name!r} have fewer than {floor} characters "
                    f"in {mark['text_column']!r} (Unicode code points). Every row of the split "
                    "meets its floor; drop the short rows before the split, or lower the floor.",
                    "wsp_long_rows_below_floor",
                    {
                        "split": name,
                        "min_chars": floor,
                        "count": short,
                        "sample_keys": [str(k) for k in sample],
                    },
                )
        lengths[name] = length
    for name in sorted(heldout & set(paths)):  # (4)
        for other, other_path in written:
            if other in heldout:
                continue  # two held-out sets may share rows (R1A-D14)
            count, keys = _shared_keys(paths[name], [other_path])
            if count:
                raise _refuse(
                    f"{count} row(s) of WSP held-out split {name!r} are exact duplicates of rows "
                    f"in split {other!r}. A held-out partition shares no row with any other "
                    "split of the version; remove the duplicates before the split step.",
                    "wsp_heldout_shares_rows",
                    {"splits": [name, other], "count": count, "sample_keys": keys},
                )
    inherited = input_heldout(session, version_input_ids)
    for item in inherited:  # (5)
        if roles_declared_by_step:
            mine = marks.get(item["split"])
            role = split_roles.get(item["split"]) or {}
            if mine != item["mark"] or not role.get("held_out"):
                raise _refuse(
                    f"Split {item['split']!r} is a WSP held-out partition in input version "
                    f"{item['version_id']}, and a step of this build declares it "
                    + ("not at all" if item["split"] not in split_roles else "otherwise")
                    + ". A held-out partition stays held out with the same mark; declare it "
                    "again with the same role, set kind, text column and floor.",
                    "wsp_heldout_redeclared",
                    {
                        "split": item["split"],
                        "version_id": item["version_id"],
                        "inherited": item["mark"],
                        "declared": mine,
                    },
                )
        for other, other_path in written:
            if other in heldout:
                continue
            count, keys = _shared_keys(item["path"], [other_path])
            if count:
                raise _refuse(
                    f"{count} row(s) of WSP held-out split {item['split']!r} of input version "
                    f"{item['version_id']} are in split {other!r} of this build. Held-out rows "
                    "never leave a held-out partition (exact duplicates); keep them in it.",
                    "wsp_heldout_rows_left_heldout",
                    {
                        "split": item["split"],
                        "version_id": item["version_id"],
                        "into": other,
                        "count": count,
                        "sample_keys": keys,
                    },
                )
    for name in sorted(heldout & set(paths)):  # (6)
        generated = held_out_violations(paths[name])
        if generated:
            raise _refuse(
                f"WSP held-out split {name!r} contains generated rows. A WSP held-out partition "
                "holds only rows from sources, whatever its direction or proof (P43): leave the "
                "split unmarked, or keep generated rows out of it (a step that adds rows after "
                "the split can still put them in it).",
                GENERATED_IN_HELDOUT,
                {"split": name, "sample_keys": generated},
            )
    by_split: dict[str, list[Path]] = {}
    for item in inherited:
        by_split.setdefault(str(item["split"]), []).append(item["path"])
    # (7) operator decision 2026-11-25; after (6), so P43 names a generated row first
    for split, inherited_paths in sorted(by_split.items()):
        if split not in paths:
            continue
        count, keys = _added_rows(paths[split], inherited_paths)
        if count:
            raise _refuse(
                f"WSP held-out split {split!r} gained {count} row(s) its input versions did not "
                "hold there. An inherited held-out partition keeps exactly its rows; a larger "
                "held-out set is a new, separately declared split (dataworks_howto "
                "wsp_partitions).",
                "wsp_heldout_grew",
                {"split": split, "added": count, "sample_keys": keys},
            )
    return lengths


# --- build sites (FR-021.9, FR-021.10; FTID I7) ------------------------------------------------


def shipped_wsp(version_split: Mapping[str, Any], shipped_path: Path) -> dict[str, Any] | None:
    """A manifest split's ``wsp``: ``None`` for an unmarked split, else EXACTLY ``{role, set_kind,
    text_column, min_chars}`` copied key by key from the stored mark plus ``length`` measured over
    the SHIPPED file (the projection may omit rows; FR-021.10). Never the stored mark spread: it
    carries ``version_length``, which the closed schema refuses (R2B-10). The only producer of a
    manifest's ``wsp``; the publish build and the export call it once per split. Refuses
    ``wsp_partition_empty`` (409) when the shipped file has no rows (FR-021.9)."""
    from ..core.errors import AppError

    stored = version_split.get("wsp")
    if not stored:
        return None
    mark = {key: stored[key] for key in MARK_KEYS}
    if _text_values(shipped_path, str(mark["text_column"])) is None:
        raise AppError(
            f"WSP split {version_split['name']!r} ships without its text column "
            f"{mark['text_column']!r}, so its character lengths cannot be stated. Include the "
            "column in the files.",
            code="wsp_text_column_invalid",
            status_code=409,
            details={"split": str(version_split["name"]), "text_column": mark["text_column"]},
        )
    length, per_row = measure(shipped_path, str(mark["text_column"]))
    if len(per_row) == 0:
        raise AppError(
            f"WSP split {version_split['name']!r} ships no rows: the label projection left none. "
            "A WSP partition must hold rows; choose another label column, or resolve the rows it "
            "omits.",
            code="wsp_partition_empty",
            status_code=409,
            details={"split": str(version_split["name"])},
        )
    return {**mark, "length": length}
