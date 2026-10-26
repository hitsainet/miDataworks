"""What feature 008 reads about generated rows (FTDD 007 section 2.3 row 008; P-14; FTASKS 8.9).

- :func:`generators_for_runs`: each bound generation run's generator identities (model, revision
  or "not reported", steering hash or "none"), pinning and revision flags — for the card, the
  manifest and C-4's model-terms check (the generator's model must have a terms note too).
- :func:`diversity_finding`: the newest diversity report's verdict for a version; ``falls``
  becomes 008's caveat ``diversity_falling`` (warn only, P-01).

008 reaches this module through its one seam (``services/publishing/feature_seams.py``).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models.generation import DiversityReport, GenerationRun


def generators_for_runs(run_ids: Sequence[str], *, session: Session) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for run_id in run_ids:
        run = session.get(GenerationRun, run_id)
        if run is None:
            continue
        for ident in run.generator_identities:
            out.append(
                {
                    "generation_run_id": run.id,
                    "model_id": ident["model_id"],
                    "revision": ident["revision"],
                    "set_hash": ident["set_hash"],
                    "pinned": bool(run.pinned),
                    "revision_reported": bool(run.revision_reported),
                    "mode": run.mode,
                }
            )
    return out


def diversity_finding(version_id: str, *, session: Session) -> dict[str, Any] | None:
    row = session.execute(
        select(DiversityReport)
        .where(DiversityReport.version_id == version_id)
        .order_by(DiversityReport.created_at.desc(), DiversityReport.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return None
    falling = [k for k, f in row.figures.items() if f.get("verdict") == "falls"]
    return {
        "report_id": row.id,
        "verdict": row.verdict,
        "column": row.column,
        "reference_version_id": row.reference_version_id,
        "falling": falling,
    }
