"""The disjointness proof of a value-directed held-out split (feature 017 FR-017.2; FTDD 017 §4.4).

Pure. ENH-006 D10 lets generated rows sit in a held-out split when the split is chosen by the values
of declared directing columns (genre, generator, any column) and the build PROVES those values are
absent from training. One function, :func:`prove`, decides it from counted facts; two callers
gather the facts: ``split@1.1.0`` over its own input (the fast refusal) and finalize over the
WRITTEN split files (the stored, authoritative proof; ``held_out_files.gather``).

For a directed held-out split S with directing columns C(S), held-out values H(c) per column and
T(c) = the values of c over every split that is not held out, the proof passes when

- H(c) ∩ T(c) = ∅ for every c in C(S) (``intersection``);
- every row of S is held out: a single-column split's row carries a non-null value of its column in
  H(c); a "both" split's row (C(S) = every directing column) is held out on at least two of them
  (``null_rows``, ``outside_rows``).

An **identity column** (a model id such as ``generator``) is compared with its revision removed,
exactly: ``model@rev`` and ``model:rev`` read ``model`` (the last ``@`` wins, else the last ``:``);
no case folding, no alias table (TD-017.18). So a second revision of a held-out model in training
fails. Groups (``carrier_id``, ``pair_id``) shared between S and training are counted as evidence
only: D-4 stays the refusal.

**Near values (ENH-006 D42, 2026-10-24).** Matching stays exact for the decision, but a training
value that differs from a held-out value only in case or whitespace (``Fiction`` / ``fiction`` /
``Fiction ``) is recorded as evidence in ``near_values``: (column, held-out value, training value,
training rows), sorted, capped at :data:`LISTED` with ``near_values_count`` giving the total. It
never refuses; it rides the stored proof, ``extensions.held_out_proof`` and the
``generated_held_out`` caveat, so a reader sees when "absent" holds only by spelling.

Every refusal cites :data:`CITATION`, the amended rule (FR-017.6, D27).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: FR-017.6 (D27): the amended BRD-03 rule every refusal and the D-6 note cite.
CITATION = "R-03.38 as amended, ENH-006 D10"
#: The two refusal codes of a failing proof (FTDD 017 section 5.4).
NOT_DISJOINT = "held_out_not_disjoint"
VALUE_NULL = "held_out_value_null"
#: Value lists in a stored proof are capped at this many entries (the rest is counted).
LISTED = 20

Combo = tuple[str | None, ...]


def fold(value: str) -> str:
    """Case- and whitespace-folded spelling (D42 evidence only): casefold, with runs of whitespace
    collapsed to one space and the ends stripped."""
    return " ".join(value.split()).casefold()


def identity(value: str) -> str:
    """A model id without its revision: ``a@r1`` -> ``a``, ``a:r1`` -> ``a``, ``a`` -> ``a``."""
    if "@" in value:
        return value.rsplit("@", 1)[0]
    if ":" in value:
        return value.rsplit(":", 1)[0]
    return value


@dataclass(frozen=True)
class DirectedBy:
    """What directed one held-out split: stored on the split's role and its ``splits`` entry."""

    #: The directing columns of THIS split: one column, or every column for the "both" split.
    columns: tuple[str, ...]
    #: Every directing column of the step, in order (the row combos are over these).
    all_columns: tuple[str, ...]
    #: The declared held-out values of every directing column, as written in the recipe.
    held_out_values: Mapping[str, tuple[str, ...]]
    identity_columns: tuple[str, ...] = ()
    group_columns: tuple[str, ...] = ()

    @property
    def both(self) -> bool:
        return len(self.columns) > 1

    def key(self, column: str, value: str) -> str:
        return identity(value) if column in self.identity_columns else value

    def held(self, column: str) -> set[str]:
        return {self.key(column, v) for v in self.held_out_values.get(column, ())}

    def held_columns(self, combo: Combo) -> list[str]:
        """The directing columns on which a row with these values is held out."""
        return [
            c
            for c, v in zip(self.all_columns, combo, strict=True)
            if v is not None and self.key(c, v) in self.held(c)
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "columns": list(self.columns),
            "all_columns": list(self.all_columns),
            "held_out_values": {c: list(v) for c, v in self.held_out_values.items()},
            "identity_columns": list(self.identity_columns),
            "group_columns": list(self.group_columns),
        }

    @classmethod
    def from_proof(cls, proof: Mapping[str, Any]) -> DirectedBy:
        """What a stored or published proof record says directed its split: enough to recompute
        it from the split files alone (``scripts/check_held_out_proof.py``)."""
        columns = tuple(str(c) for c in proof["columns"])
        return cls(
            columns=columns,
            all_columns=columns,
            held_out_values={
                str(c): tuple(str(v) for v in vs)
                for c, vs in dict(proof["held_out_values"]).items()
            },
            identity_columns=tuple(str(c) for c in proof.get("identity_columns") or ()),
            group_columns=tuple(str(c) for c in proof.get("group_columns") or ()),
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DirectedBy:
        columns = tuple(str(c) for c in data["columns"])
        return cls(
            columns=columns,
            all_columns=tuple(str(c) for c in data.get("all_columns") or columns),
            held_out_values={
                str(c): tuple(str(v) for v in vs)
                for c, vs in dict(data.get("held_out_values") or {}).items()
            },
            identity_columns=tuple(str(c) for c in data.get("identity_columns") or ()),
            group_columns=tuple(str(c) for c in data.get("group_columns") or ()),
        )


@dataclass(frozen=True)
class Facts:
    """What the proof reads, counted by whoever read the rows."""

    #: Row count of S per combination of values over ``DirectedBy.all_columns`` (None = null).
    held_out_rows: Mapping[Combo, int]
    #: The same over every split that is not held out.
    training_rows: Mapping[Combo, int]
    #: Group column -> groups found both in S and in training.
    shared_groups: Mapping[str, int] = field(default_factory=dict)
    generated_rows: int = 0
    #: The non-held-out splits T was read from.
    training_splits: tuple[str, ...] = ()


@dataclass(frozen=True)
class Proof:
    split: str
    directed: DirectedBy
    rows: int
    generated_rows: int
    training_splits: tuple[str, ...]
    training_value_counts: Mapping[str, int]
    intersection: tuple[tuple[str, str, int], ...]
    null_rows: int
    outside: tuple[tuple[dict[str, str | None], int], ...]
    shared_groups: Mapping[str, int]
    #: D42: ``(column, held-out value, training value, training rows)``, folded matches only.
    near: tuple[tuple[str, str, str, int], ...] = ()

    @property
    def passes(self) -> bool:
        return not self.intersection and not self.null_rows and not self.outside

    def as_dict(self) -> dict[str, Any]:
        """The stored record (``dw_versions.splits[].proof``, ``Split.extensions``)."""
        return {
            "split": self.split,
            "passes": self.passes,
            "columns": list(self.directed.columns),
            "identity_columns": [
                c for c in self.directed.identity_columns if c in self.directed.columns
            ],
            "held_out_values": {
                c: sorted(self.directed.held_out_values.get(c, ())) for c in self.directed.columns
            },
            "group_columns": list(self.directed.group_columns),
            "rows": self.rows,
            "generated_rows": self.generated_rows,
            "training_splits": list(self.training_splits),
            "training_values": dict(self.training_value_counts),
            "intersection": [
                {"column": c, "value": v, "training_rows": n}
                for c, v, n in self.intersection[:LISTED]
            ],
            "intersection_count": len(self.intersection),
            "near_values": [
                {"column": c, "value": v, "training_value": t, "training_rows": n}
                for c, v, t, n in self.near[:LISTED]
            ],
            "near_values_count": len(self.near),
            "null_rows": self.null_rows,
            "outside_rows": [{"values": dict(v), "rows": n} for v, n in self.outside[:LISTED]],
            "outside_count": sum(n for _, n in self.outside),
            "shared_groups": dict(self.shared_groups),
            "rule": CITATION,
        }

    def refusal(self) -> tuple[str, str, dict[str, Any]]:
        """``(code, message, details)`` of a failing proof, naming the value and its count."""
        details: dict[str, Any] = {
            "split": self.split,
            "rule": CITATION,
            "proof": self.as_dict(),
            # the ``next`` shape of ``rules.next_step.next_for`` (kept pure: no import)
            "next": {
                "tool": "dataworks_validate_recipe",
                "args": {},
                "why": "change the held-out values or the steps so no held-out value reaches "
                "training",
            },
        }
        if self.intersection:
            column, value, n = self.intersection[0]
            details.update(column=column, value=value, count=n)
            more = len(self.intersection) - 1
            return (
                NOT_DISJOINT,
                f"Held-out value {value!r} of {column!r} is in training too ({n} row(s) outside "
                f"the held-out splits){f', and {more} more value(s)' if more > 0 else ''}: the "
                f"held-out split {self.split!r} is not disjoint from training ({CITATION}).",
                details,
            )
        if self.null_rows:
            column = self.directed.columns[0]
            details.update(column=column, count=self.null_rows)
            return (
                VALUE_NULL,
                f"{self.null_rows} row(s) of the held-out split {self.split!r} have no value in "
                f"the directing column {column!r}; a value-directed held-out split holds only "
                f"rows carrying a held-out value ({CITATION}).",
                details,
            )
        values, n = self.outside[0]
        shown = ", ".join(f"{c}={v!r}" for c, v in values.items())
        details.update(values=values, count=n)
        need = "two of its directing columns" if self.directed.both else "its directing column"
        return (
            NOT_DISJOINT,
            f"{n} row(s) of the held-out split {self.split!r} ({shown}) are not held out on "
            f"{need}: a group kept them with held-out rows. Separate the groups or hold the "
            f"value out too ({CITATION}).",
            details,
        )


def _by_column(
    rows: Mapping[Combo, int], columns: Sequence[str], directed: DirectedBy
) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {c: {} for c in columns}
    for combo, n in rows.items():
        for c, v in zip(directed.all_columns, combo, strict=True):
            if c in out and v is not None:
                k = directed.key(c, v)
                out[c][k] = out[c].get(k, 0) + int(n)
    return out


def prove(split: str, directed: DirectedBy, facts: Facts) -> Proof:
    """The proof of one directed held-out split from counted facts (FR-017.2)."""
    training = _by_column(facts.training_rows, directed.columns, directed)
    intersection: list[tuple[str, str, int]] = []
    for c in directed.columns:
        for value in sorted(directed.held(c) & set(training[c])):
            intersection.append((c, value, training[c][value]))
    near: list[tuple[str, str, str, int]] = []
    for c in directed.columns:
        held_values = directed.held(c)
        folded: dict[str, list[str]] = {}
        for h in held_values:
            folded.setdefault(fold(h), []).append(h)
        for t, n in training[c].items():
            if t in held_values:
                continue  # an exact match is the intersection, not a near value
            for h in folded.get(fold(t), ()):
                near.append((c, h, t, n))
    near.sort()
    null_rows = 0
    outside: list[tuple[dict[str, str | None], int]] = []
    need = 2 if directed.both else 1
    for combo, n in sorted(facts.held_out_rows.items(), key=lambda kv: [str(x) for x in kv[0]]):
        held = directed.held_columns(combo)
        if directed.both:
            ok = len(held) >= need
        else:
            ok = directed.columns[0] in held
        if ok:
            continue
        values = dict(zip(directed.all_columns, combo, strict=True))
        if not directed.both and values[directed.columns[0]] is None:
            null_rows += int(n)
        else:
            outside.append((values, int(n)))
    return Proof(
        split=split,
        directed=directed,
        rows=sum(int(n) for n in facts.held_out_rows.values()),
        generated_rows=int(facts.generated_rows),
        training_splits=tuple(facts.training_splits),
        training_value_counts={c: len(training[c]) for c in directed.columns},
        intersection=tuple(intersection),
        null_rows=null_rows,
        outside=tuple(outside),
        shared_groups=dict(facts.shared_groups),
        near=tuple(near),
    )


def count_combos(columns: Iterable[Sequence[str | None]], rows: Iterable[int]) -> dict[Combo, int]:
    """Row count per value combination, over the given per-row column lists, for ``rows``."""
    cols = list(columns)
    out: dict[Combo, int] = {}
    for i in rows:
        combo = tuple(col[i] for col in cols)
        out[combo] = out.get(combo, 0) + 1
    return out
