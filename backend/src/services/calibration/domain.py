"""The calibration-domain warning (FR-006.36, T-27).

Compares the character-length profile of the calibration set's version with that of every other
version labeled under the same labeler identity. 009's length profile (FR-009.9) and its check
(FR-009.10) are not served yet (searched ``ls backend/src/services/detector*`` on 2026-10-07:
nothing), so the profile is computed here with FR-009.9's definition — characters of the content
columns — and FR-009.10's statistic, quantile overlap. The swap to 009's function is filed as a
follow-up. A warning never changes a verdict.

Note: FR-006.36 says "median-ratio band" while FR-009.10 (decided later, T-44) uses quantile
overlap; 009's rule is the one this check must match, so overlap decides and both medians are
reported. The tolerance below is PROVISIONAL until 009 measures it on the prototype's sets (T-44).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from ...models.enums import ColumnRole
from ..duck import connect, files_param, quote_ident, top_level_columns
from ..label_inputs import version_files

#: Provisional (see the module docstring).
OVERLAP_TOLERANCE = 0.8
SAMPLE_ROWS = 20_000


def content_lengths(
    splits: Sequence[Mapping[str, Any]], roles: Mapping[str, str]
) -> np.ndarray | None:
    files = version_files(splits)
    if not files:
        return None
    con = connect()
    try:
        columns = top_level_columns(con, files)
        content = [c for c, r in roles.items() if r == ColumnRole.CONTENT and c in columns]
        if not content:
            return None
        expr = " + ".join(
            f"coalesce(length(CAST({quote_ident(c, columns)} AS VARCHAR)), 0)" for c in content
        )
        rows = con.execute(
            f"SELECT {expr} FROM read_parquet(?) "  # noqa: S608 - identifiers quoted
            f"USING SAMPLE reservoir({SAMPLE_ROWS} ROWS) REPEATABLE (7)",
            [files_param(files)],
        ).fetchall()
    finally:
        con.close()
    return np.array([r[0] for r in rows], dtype=np.float64) if rows else None


def profile(lengths: np.ndarray) -> dict[str, float | int]:
    return {
        "median": float(np.median(lengths)),
        "q1": float(np.quantile(lengths, 0.25)),
        "q3": float(np.quantile(lengths, 0.75)),
        "p95": float(np.quantile(lengths, 0.95)),
        "rows": int(len(lengths)),
    }


def quantile_overlap(a: np.ndarray, b: np.ndarray) -> float:
    """The smaller of: share of ``a`` inside ``b``'s 5th–95th percentile range, and vice versa."""
    a_lo, a_hi = np.quantile(a, [0.05, 0.95])
    b_lo, b_hi = np.quantile(b, [0.05, 0.95])
    in_b = float(((a >= b_lo) & (a <= b_hi)).mean())
    in_a = float(((b >= a_lo) & (b <= a_hi)).mean())
    return min(in_a, in_b)


def domain_warnings(
    set_lengths: np.ndarray | None, others: Mapping[str, np.ndarray | None]
) -> list[dict[str, Any]]:
    """One warning per labeled version whose length profile differs from the set's."""
    if set_lengths is None or not len(set_lengths):
        return []
    out: list[dict[str, Any]] = []
    for version_id, lengths in sorted(others.items()):
        if lengths is None or not len(lengths):
            continue
        overlap = quantile_overlap(set_lengths, lengths)
        if overlap < OVERLAP_TOLERANCE:
            s, o = profile(set_lengths), profile(lengths)
            out.append(
                {
                    "kind": "calibration_domain",
                    "version_id": version_id,
                    "overlap": overlap,
                    "tolerance": OVERLAP_TOLERANCE,
                    "tolerance_provisional": True,
                    "calibration": s,
                    "labeled": o,
                    "message": (
                        f"The calibration set's texts (median {s['median']:.0f} characters, "
                        f"{s['rows']} rows) differ in length from version {version_id}'s "
                        f"(median {o['median']:.0f} characters, {o['rows']} rows); overlap "
                        f"{overlap:.2f}. Trust the verdict only for texts like the set's."
                    ),
                }
            )
    return out
