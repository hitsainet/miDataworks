"""Train/evaluation leakage across splits or roles (FR-004.20, 004.21, 004.50; FTID 004 §7.6).

The "sides" of a check are the inputs: ``(version, split)`` pairs (feature 008's C-3 passes one per
split) or ``(version, split, role)`` triples (feature 009's D-4). A single bare version is checked
across its own splits. Three kinds of crossing are reported, each as counts per side pair keyed
``"<a>|<b>"`` (names sorted) and as listed pairs:

- **exact**: rows on two sides with the same row key (feature 002's), or with the same text. The key
  hashes every content column, so identical text under a different row number (an index source's
  ``idx``) has another key and is still the same text;
- **near**: MinHash candidates verified at or above the threshold, across sides (``basis: lexical``;
  embeddings after M1, P-18), over the same text. A text shorter than one shingle is one shingle, so
  two short texts are near only when their words match. Since FU-7a gap (p) (``near_pairs:
  NEAR_PAIRS`` in the parameters) EVERY crossing pair of a band's bucket is verified, not only each
  member against the bucket's first, and the bands are laid out for recall at the threshold
  (:func:`minhash.recall_layout`). Before, a near-duplicate cluster of two fresh rows and one train
  row reported one pair; a repair dropped that fresh row and the re-check found the other: a repair
  was no proof. Every near pair is a function of its two texts alone now, so a re-check after a
  repair finds nothing the first check did not;
- **group**: rows sharing a value of a named group column on two sides (two edits of one
  Humicroedit headline, ``scripts/paired_probe.py``) — texts may differ, the topic still leaks.

The text compared is the content columns that hold text (:func:`duck.holds_text`). A number is
identity, not text: with ``idx`` in it, identical text read as different and short text never matched,
and D-4 read green over a group of identical text on two splits (dry run, 2026-11-01). A row with no
text is compared by key alone: blank is not a duplicate of blank.

The pairs are written to ``runs/<report id>/reports/leakage_pairs.parquet`` with excerpts.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy.orm import Session

from ...core.config import get_settings
from ...core.storage import run_dir, staged_path
from ...models.enums import ColumnRole
from ..duck import TEXT_CONTENT, holds_text
from ..identity import digest_text, file_sha256
from . import minhash
from .audit_service import excerpt
from .codes import ReportInput, files_for, load_table, schema_names
from .errors import CurationError
from .text_stats import as_text

DEFAULT_THRESHOLD = 0.8
DEFAULT_SHINGLE = "word"
DEFAULT_SIZE = 5
PAIRS_FILE = "leakage_pairs.parquet"
SIDE = "_side"
#: Per-input grouping (009 detector sets): one string column ``"<column>\x1f<value>"`` per row, so
#: each input is grouped by ITS OWN column and an input that declared none contributes no group.
GROUP = "_dw_group"
GROUP_SEP = "\x1f"
#: Part of every report's identity: a report stored before text was compared this way does not
#: answer for this behaviour. Change it when what the check compares changes.
COMPARISON = TEXT_CONTENT
#: Part of every API report's identity (FU-7a gap (p), 2026-11-27): which near pairs are reported.
#: Every crossing pair of every band bucket, under :func:`minhash.recall_layout`. The
#: ``leakage_check@1.0.0`` recipe step does not pass it and keeps its first reading (each bucket's
#: members against its first member, :func:`minhash.band_layout`), so a rebuilt recipe's report is
#: unchanged.
NEAR_PAIRS = "every_crossing_pair/v1"
#: The chance that a pair exactly AT the threshold is a candidate, under ``NEAR_PAIRS``.
NEAR_RECALL = 0.9
#: Signature comparisons one check may make under ``NEAR_PAIRS`` before it refuses: a band bucket
#: of tens of thousands of distinct near-identical texts on two sides is a dataset to deduplicate
#: first, not a check to run for an hour.
MAX_NEAR_COMPARISONS = 200_000_000


def leakage_params(
    group_column: str | None,
    threshold: float | None,
    group_columns: dict[str, str | None] | None = None,
    against_version_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """``group_columns`` (role -> that role's group column, or ``None`` for no grouping) groups
    each input by its OWN column; it is added only when given. ``against_version_ids`` (FU-7a gap
    (n)) is added only when given, so every earlier report keeps its identity."""
    params: dict[str, Any] = {
        "group_column": group_column,
        "threshold": float(threshold if threshold is not None else DEFAULT_THRESHOLD),
        "permutations": get_settings().curation_minhash_permutations,
        "shingle": DEFAULT_SHINGLE,
        "size": DEFAULT_SIZE,
        "comparison": COMPARISON,
        "near_pairs": NEAR_PAIRS,
    }
    if group_columns is not None:
        if group_column is not None:
            raise ValueError("give group_column or group_columns, not both")
        params["group_columns"] = dict(sorted(group_columns.items()))
    if against_version_ids:
        params["against_version_ids"] = list(dict.fromkeys(str(v) for v in against_version_ids))
    return params


#: An against version's split is the side ``"<split>@<version id>"``: a name it cannot share with a
#: split of the version under check (both can have ``train``).
AGAINST_SEP = "@"


def against_inputs(
    session: Session, version_id: str, against_version_ids: Sequence[str]
) -> list[ReportInput]:
    """The inputs of a check of ``version_id``'s splits against other versions' splits (FU-7a gap
    (n): CHK1 had to build a concatenation of four versions to ask this). The version under check
    is a bare input (one side per split, as always); each other version's split is its own side,
    ``"<split>@<version id>"``. Pairs inside the other versions are not reported
    (:func:`check`)."""
    from .api import require_version

    require_version(session, version_id)
    inputs = [ReportInput(version_id)]
    for other in dict.fromkeys(str(v) for v in against_version_ids):
        if other == str(version_id):
            raise CurationError(
                "against_self",
                "A version is checked against OTHER versions; leave the version itself out of "
                "against_version_ids (its own splits are always compared with each other).",
                {"version_id": other},
            )
        version = require_version(session, other)
        for split in version.splits:
            name = str(split["name"])
            inputs.append(ReportInput(other, name, f"{name}{AGAINST_SEP}{other}"))
    return inputs


@dataclass
class LeakageResult:
    """FTDD §4.2; feature 008's seam reads ``exact_pairs``, ``near_pairs`` and ``group_pairs``."""

    inputs: list[dict[str, Any]]
    exact_pairs: dict[str, int] = field(default_factory=dict)
    near_pairs: dict[str, int] = field(default_factory=dict)
    group_pairs: dict[str, int] = field(default_factory=dict)
    pairs_artefact: str | None = None
    basis: str = "lexical"
    threshold: float = DEFAULT_THRESHOLD
    group_column: str | None = None
    report_id: str | None = None
    n_rows: int = 0

    @classmethod
    def from_report(cls, report: Any) -> LeakageResult:
        r = report.result
        return cls(
            inputs=list(report.inputs),
            exact_pairs=dict(r["exact_pairs"]),
            near_pairs=dict(r["near_pairs"]),
            group_pairs=dict(r["group_pairs"]),
            pairs_artefact=r.get("pairs_artefact"),
            basis=r["basis"],
            threshold=float(r["threshold"]),
            group_column=r.get("group_column"),
            report_id=report.id,
            n_rows=int(r["n_rows"]),
        )

    @property
    def total(self) -> int:
        return (
            sum(self.exact_pairs.values())
            + sum(self.near_pairs.values())
            + sum(self.group_pairs.values())
        )


def _key(a: str, b: str) -> str:
    x, y = sorted((a, b))
    return f"{x}|{y}"


def _crossing(rows: Sequence[int], sides: Sequence[str]) -> Iterator[tuple[int, int]]:
    """The pairs of ``rows`` on two different sides, each ``(lower, higher)``. Same-side pairs are
    never formed: one text on 50,000 training rows and one test row is 50,000 pairs to report, not a
    billion to look at."""
    by_side: dict[str, list[int]] = {}
    for i in rows:
        by_side.setdefault(sides[i], []).append(i)
    for a, b in combinations(by_side.values(), 2):
        for i in a:
            for j in b:
                yield min(i, j), max(i, j)


def load_sides(
    session: Session,
    inputs: Sequence[ReportInput],
    group_column: str | None,
    group_columns: dict[str, str | None] | None = None,
    *,
    lenient: frozenset[str] = frozenset(),
) -> tuple[pa.Table, list[str]]:
    """Every input's rows with a ``_side`` column; a bare version contributes one side per split.

    With ``group_columns`` each input (by its role) is grouped by its own column into ``GROUP``;
    an input whose role declared no column has no group, while one that DECLARED a column its
    version lacks is refused, naming the role. A version in ``lenient`` (an against version) that
    lacks ``group_column`` has no group values instead: the version under check still refuses a
    misspelt column."""
    from .api import require_version

    tables = []
    content: set[str] = set()
    for ri in inputs:
        version = require_version(session, ri.version_id)
        content |= {c for c, r in version.column_roles.items() if r == ColumnRole.CONTENT}
        source = files_for(version, ri)
        own = (group_columns or {}).get(str(ri.role)) if group_columns is not None else None
        for split, path in source.files:
            one = type(source)(ri, [(split, path)])
            names = schema_names([one])
            missing = group_column is not None and group_column not in names
            if missing and str(ri.version_id) not in lenient:
                raise CurationError(
                    "group_column_missing",
                    f"The group column {group_column!r} is not in {ri.version_id}; choose a "
                    "column the version has.",
                    {"group_column": group_column},
                )
            if own is not None and own not in names:
                raise CurationError(
                    "group_column_missing",
                    f"Role {ri.role} declares the group column {own!r}, which is not in "
                    f"{ri.version_id}; choose a column that version has.",
                    {"group_column": own, "role": ri.role, "version_id": str(ri.version_id)},
                )
            wanted = ["_dw_row_key", "_dw_occurrence", *sorted(c for c in content if c in names)]
            if group_column:
                wanted.append(group_column)
            if own is not None and own not in wanted:
                wanted.append(own)
            table = _rendered(load_table([one], wanted), content)
            if missing and group_column is not None:  # load_table projected it as NULL
                table = table.set_column(
                    table.schema.get_field_index(group_column),
                    group_column,
                    pa.nulls(table.num_rows, pa.string()),
                )
            if group_columns is not None:
                values = table.column(own).to_pylist() if own is not None else []
                groups = (
                    [None if v is None else f"{own}{GROUP_SEP}{v}" for v in values]
                    if own is not None
                    else [None] * table.num_rows
                )
                if own is not None and own not in content:
                    table = table.drop_columns([own])
                table = table.append_column(GROUP, pa.array(groups, pa.string()))
            name = ri.role or split
            tables.append(table.append_column(SIDE, pa.array([name] * table.num_rows, pa.string())))
    sides = sorted({str(t.column(SIDE)[0]) for t in tables if t.num_rows})
    if len(sides) < 2:
        raise CurationError(
            "no_splits",
            "Leakage is measured between splits or roles, and these inputs have only "
            f"{len(sides)}. Add a split step, or check several roles together.",
            {"sides": sides},
        )
    merged = pa.concat_tables(tables, promote_options="default")
    return merged, sorted(content)


def _rendered(table: pa.Table, content: set[str]) -> pa.Table:
    """``table`` with every nested content column (chat messages: ``list<struct<role, content>>``)
    rendered to the text ``check_table`` compares (``as_text``: the message contents, one per line).

    One role's column can hold chat messages while another's holds a string (live finding
    2026-11-19); the sides are merged into one table, which needs one type per column. ``as_text``
    returns a string unchanged, so rendering here gives ``check_table`` the same text it would have
    built from the nested values: a report over chat columns alone is unchanged."""
    for name in sorted(content & set(table.schema.names)):
        data_type = table.schema.field(name).type
        if (
            pa.types.is_list(data_type)
            or pa.types.is_large_list(data_type)
            or pa.types.is_struct(data_type)
        ):
            text = pa.array([as_text(v) for v in table.column(name).to_pylist()], pa.string())
            table = table.set_column(table.schema.get_field_index(name), name, text)
    return table


def check_table(
    table: pa.Table,
    content: Sequence[str],
    params: dict[str, Any],
    seed: int,
    *,
    subject: frozenset[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Exact, near and group crossings between the ``_side`` values of ``table``. With
    ``subject``, a pair counts only when one of its sides is in it (an against check)."""
    group_column = params.get("group_column")
    per_input = params.get("group_columns") is not None
    group_source = GROUP if per_input else group_column
    n = table.num_rows
    sides = [str(s) for s in table.column(SIDE).to_pylist()]
    keys = [str(k) for k in table.column("_dw_row_key").to_pylist()]
    columns = [
        table.column(c).to_pylist()
        for c in content
        if c in table.schema.names and holds_text(table.schema.field(c).type)
    ]
    texts = ["\n".join(as_text(v) for v in vals) for vals in zip(*columns, strict=True)]
    if not columns:
        texts = [""] * n
    pairs: list[dict[str, Any]] = []
    exact: dict[str, int] = {}
    near: dict[str, int] = {}
    group: dict[str, int] = {}

    def add(
        kind: str, i: int, j: int, statistic: float, bucket: dict[str, int], shared: str
    ) -> None:
        if sides[i] == sides[j]:
            return
        if subject is not None and sides[i] not in subject and sides[j] not in subject:
            return
        name = _key(sides[i], sides[j])
        bucket[name] = bucket.get(name, 0) + 1
        pairs.append(
            {
                "kind": kind,
                "side_a": sides[i],
                "side_b": sides[j],
                "row_key_a": keys[i],
                "row_key_b": keys[j],
                "statistic": statistic,
                "shared": shared,
                "excerpt_a": excerpt(texts[i]),
                "excerpt_b": excerpt(texts[j]),
            }
        )

    by_key: dict[str, list[int]] = {}
    for i, k in enumerate(keys):
        by_key.setdefault(k, []).append(i)
    for k, rows in by_key.items():
        for i, j in combinations(rows, 2):
            add("exact", i, j, 1.0, exact, k)
    by_text: dict[str, list[int]] = {}
    for i, value in enumerate(texts):
        if value.strip():
            by_text.setdefault(value, []).append(i)
    for value, rows in by_text.items():
        for i, j in _crossing(rows, sides):
            if keys[i] != keys[j]:  # identical keys are already exact pairs
                add("exact", i, j, 1.0, exact, digest_text(value))
    threshold = float(params["threshold"])
    if params.get("near_pairs") == NEAR_PAIRS:
        layout = minhash.recall_layout(int(params["permutations"]), threshold, NEAR_RECALL)
        verified = crossing_near_pairs(texts, sides, params, seed, layout, threshold)
    else:  # leakage_check@1.0.0's reading, kept for rebuilt recipes
        sigs = minhash.signatures_for(
            texts,
            kind=params["shingle"],
            size=int(params["size"]),
            count=int(params["permutations"]),
            seed=seed,
        )
        layout = minhash.band_layout(int(params["permutations"]), threshold)
        _, verified = minhash.near_duplicate_groups(sigs, layout, threshold)
    for (i, j), estimate in verified.items():
        identical = keys[i] == keys[j] or texts[i] == texts[j]  # already exact pairs
        if not identical and texts[i].strip() and texts[j].strip():
            add("near", i, j, estimate, near, "")
    if group_source:
        values = table.column(group_source).to_pylist()
        by_group: dict[str, list[int]] = {}
        for i, v in enumerate(values):
            if v is not None:
                by_group.setdefault(str(v), []).append(i)
        for value, rows in by_group.items():
            if len({sides[i] for i in rows}) < 2:
                continue
            shared = value.split(GROUP_SEP, 1)[-1] if per_input else value
            for i, j in combinations(rows, 2):
                add("group", i, j, 1.0, group, shared)
    result = {
        "sides": sorted(set(sides)),
        "exact_pairs": exact,
        "near_pairs": near,
        "group_pairs": group,
        "pairs_artefact": None,
        "basis": "lexical",
        "threshold": threshold,
        "band_layout": {"bands": layout.bands, "rows": layout.rows},
        "group_column": group_column,
        **({"group_columns": params["group_columns"]} if per_input else {}),
        "n_rows": n,
        "pairs_total": len(pairs),
    }
    if subject is not None:
        result["subject_sides"] = sorted(subject)
    return result, pairs


def crossing_near_pairs(
    texts: Sequence[str],
    sides: Sequence[str],
    params: dict[str, Any],
    seed: int,
    layout: minhash.BandLayout,
    threshold: float,
) -> dict[tuple[int, int], float]:
    """Every pair of rows on two different sides, with different non-blank texts, whose signatures
    share a band of ``layout`` and agree in ``threshold`` of their positions or more; ``(lower,
    higher) -> estimate``, in row order.

    Complete over the candidates, which ``minhash.candidate_pairs`` is not: it links each bucket's
    members to its FIRST member only (enough for deduplication's clusters), so of two fresh rows
    near one train row, only the first was paired (FU-7a gap (p)). The work is done per distinct
    signature, so many copies of one short template cost one comparison, and only pairs that can
    cross two sides are compared."""
    items: dict[str, list[int]] = {}
    for i, value in enumerate(texts):
        if value.strip():
            items.setdefault(value, []).append(i)
    if not items:
        return {}
    item_rows = list(items.values())
    sigs = minhash.signatures_for(
        list(items),
        kind=params["shingle"],
        size=int(params["size"]),
        count=int(params["permutations"]),
        seed=seed,
    )
    # one class per distinct signature: its texts, and the sides its rows sit on
    by_signature: dict[bytes, int] = {}
    class_items: list[list[int]] = []
    for u in range(len(item_rows)):
        c = by_signature.setdefault(sigs[u].tobytes(), len(class_items))
        if c == len(class_items):
            class_items.append([])
        class_items[c].append(u)
    class_sides = [
        frozenset(sides[i] for u in members for i in item_rows[u]) for members in class_items
    ]
    reps = sigs[[members[0] for members in class_items]]
    keys = minhash.band_keys(reps, layout)
    found: dict[tuple[int, int], float] = {}

    def emit(us: Sequence[int], vs: Sequence[int], estimate: float) -> None:
        for u in us:
            for v in vs:
                if u == v:
                    continue
                for i in item_rows[u]:
                    for j in item_rows[v]:
                        if sides[i] != sides[j]:
                            found[(min(i, j), max(i, j))] = estimate

    for c, members in enumerate(class_items):  # different texts, one signature: estimate 1.0
        if len(members) > 1 and len(class_sides[c]) > 1:
            for a, b in combinations(members, 2):
                emit([a], [b], 1.0)
    compared = 0
    done: set[tuple[int, int]] = set()
    for band in range(layout.bands):
        column = keys[:, band]
        order = np.argsort(column, kind="stable")
        sorted_keys = column[order]
        starts = np.flatnonzero(np.r_[True, sorted_keys[1:] != sorted_keys[:-1]])
        ends = np.r_[starts[1:], len(order)]
        for s, e in zip(starts, ends, strict=True):
            if e - s < 2:
                continue
            bucket = sorted(int(c) for c in order[s:e])
            if len(set().union(*(class_sides[c] for c in bucket))) < 2:
                continue  # every row of the bucket on one side: nothing can cross
            for x, y in _crossable(bucket, class_sides):
                compared += len(y)
                if compared > MAX_NEAR_COMPARISONS:
                    raise CurationError(
                        "leakage_too_many_candidates",
                        f"The near-duplicate check would compare more than "
                        f"{MAX_NEAR_COMPARISONS:,} candidate pairs: a band holds {len(bucket):,} "
                        "distinct near-identical texts on two sides. Remove near duplicates "
                        "inside each split first (dedup_minhash), or raise the threshold.",
                        {"bucket": len(bucket), "limit": MAX_NEAR_COMPARISONS},
                    )
                others = np.array(y, dtype=np.int64)
                estimates = (reps[others] == reps[x]).mean(axis=1)
                for other, estimate in zip(y, estimates.tolist(), strict=True):
                    pair = (min(x, other), max(x, other))
                    if estimate < threshold or pair in done:
                        continue
                    done.add(pair)
                    emit(class_items[pair[0]], class_items[pair[1]], float(estimate))
    return dict(sorted(found.items()))


def _crossable(
    bucket: Sequence[int], class_sides: Sequence[frozenset[str]]
) -> Iterator[tuple[int, list[int]]]:
    """``(x, others)`` covering every pair of ``bucket`` whose rows can sit on two sides, each pair
    once: a class on several sides against every later class, and between two one-side groups the
    smaller group's classes against the whole larger group."""
    multi = [c for c in bucket if len(class_sides[c]) > 1]
    single: dict[str, list[int]] = {}
    for c in bucket:
        if len(class_sides[c]) == 1:
            single.setdefault(next(iter(class_sides[c])), []).append(c)
    for k, x in enumerate(multi):
        others = multi[k + 1 :] + [c for group in single.values() for c in group]
        if others:
            yield x, others
    for a, b in combinations(sorted(single), 2):
        small, large = sorted((single[a], single[b]), key=len)
        for x in small:
            yield x, large


def check(
    session: Session,
    inputs: Sequence[ReportInput],
    params: dict[str, Any],
    seed: int,
    *,
    report_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    against = frozenset(params.get("against_version_ids") or ())
    table, content = load_sides(
        session, inputs, params.get("group_column"), params.get("group_columns"), lenient=against
    )
    subject = _subject_sides(session, inputs, against) if against else None
    result, pairs = check_table(table, content, params, seed, subject=subject)
    artefacts: list[dict[str, Any]] = []
    if pairs:
        destination = run_dir(report_id) / "reports" / PAIRS_FILE
        destination.parent.mkdir(parents=True, exist_ok=True)
        with staged_path(destination) as staged:
            pq.write_table(pa.Table.from_pylist(pairs), staged)
        path_text = f"runs/{report_id}/reports/{PAIRS_FILE}"
        result["pairs_artefact"] = path_text
        artefacts.append(
            {
                "name": PAIRS_FILE,
                "path": path_text,
                "sha256": file_sha256(destination),
                "rows": len(pairs),
            }
        )
    return result, artefacts, table.num_rows


def _subject_sides(
    session: Session, inputs: Sequence[ReportInput], against: frozenset[str]
) -> frozenset[str]:
    """The side names of the inputs that are NOT against versions (named as ``load_sides`` names
    them)."""
    from .api import require_version

    names: set[str] = set()
    for ri in inputs:
        if str(ri.version_id) in against:
            continue
        for split, _ in files_for(require_version(session, ri.version_id), ri).files:
            names.add(ri.role or split)
    return frozenset(names)


def compute_leakage(
    session: Session, inputs: list[ReportInput], params: dict[str, Any], seed: int
) -> Any:
    from .report_service import Computed

    result, artefacts, rows = check(session, inputs, params, seed, report_id=str(uuid.uuid4()))
    return Computed(result=result, artefacts=artefacts, rows=rows)


def read_pairs(report: Any, *, page: int, limit: int) -> dict[str, Any]:
    """One page of a leakage report's pairs (the route never returns more than one page)."""
    from ...core.storage import resolve_under_data_dir

    path = (report.result or {}).get("pairs_artefact")
    if not path:
        return {"pairs": [], "total": 0}
    table = pq.read_table(resolve_under_data_dir(path))
    rows = table.slice(page * limit, limit).to_pylist()
    return {"pairs": rows, "total": table.num_rows}
