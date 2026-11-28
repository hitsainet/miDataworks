"""Fixtures for feature 006: a version with human-label columns, completed label runs with real
``dw_labels`` rows, and an in-process runner for the calibration job.

Fixture discipline (FTDD 006 section 10.2): scores and human labels are generated INDEPENDENTLY
(a noisy signal, not a copy of the label), group and stratum columns disagree with the label for
some rows, and ratings are unsorted unless a test asks for sorted ones.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy import select

from src.core.canonical_json import canonical_json
from src.core.database import sync_session_factory
from src.models.job import Job
from src.models.label import Label
from src.models.label_run import LabelRun
from src.services.calibration.mapping import question_hash
from src.services.labeling_rules import fingerprint, identity_hash, labeler_identity
from tests.support import db_factories

QUESTION = "Would most readers find this text funny?"
LABELS = ["humorous", "not_humorous"]


def row_key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def make_version(
    data_dir: Path,
    rows: Sequence[Mapping[str, Any]],
    *,
    content: Sequence[str] = ("text",),
    roles: Mapping[str, str] | None = None,
    bindings: Sequence[Mapping[str, Any]] = (),
) -> str:
    """A completed version whose split file holds ``rows`` (each must have ``text``). Its
    manifest names no source (``private_only`` by 008's table) and ``bindings`` are bound runs."""
    version_id = db_factories.uid()
    relative = f"versions/{version_id}/train.parquet"
    path = data_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    columns: dict[str, list[Any]] = {
        "_dw_row_key": [r.get("_dw_row_key", row_key(str(r["text"]))) for r in rows],
        "_dw_occurrence": [0] * len(rows),
        "_dw_origin": ["source"] * len(rows),
        "_dw_split": ["train"] * len(rows),
    }
    names = [k for k in rows[0] if k != "_dw_row_key"] if rows else ["text"]
    for name in names:
        columns[name] = [r.get(name) for r in rows]
    pq.write_table(pa.table(columns), path)
    column_roles = {n: ("content" if n in content else "metadata") for n in names}
    column_roles.update(roles or {})
    manifest = canonical_json({"format": "calibration-fixture", "id": version_id, "sources": []})
    with sync_session_factory()() as db:
        db_factories.version(
            db,
            id=version_id,
            splits=[
                {
                    "name": "train",
                    "path": relative,
                    "rows": len(rows),
                    "held_out": False,
                    "bytes": path.stat().st_size,
                    "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "logical_digest": hashlib.sha256(b"calibration-fixture").hexdigest(),
                }
            ],
            total_rows=len(rows),
            column_roles=column_roles,
            bindings=[dict(b) for b in bindings],
            manifest=manifest,
            manifest_sha256=hashlib.sha256(manifest).hexdigest(),
        )
        db.commit()
    return version_id


def humor_rows(
    n: int = 400, seed: int = 0, *, sorted_ratings: bool = False
) -> list[dict[str, Any]]:
    """Edited rows with a meanGrade, five ratings and a pair group; an original per group."""
    rng = np.random.default_rng(seed)
    rows: list[dict[str, Any]] = []
    for g in range(n // 4):
        pair = f"p{g:04d}"
        rows.append(
            {
                "text": f"original headline {g}",
                "meanGrade": None,
                # As in the real prepared file (scripts/prepare.py): an original has no grades,
                # stored as an EMPTY string, not null. A null here hid a live import defect.
                "grades": "",
                "pair_id": pair,
                "kind": "original",
            }
        )
        for e in range(3):
            grades = rng.integers(0, 4, 5).tolist()
            if sorted_ratings:
                grades = sorted(grades, reverse=True)
            mean = float(np.mean(grades))
            rows.append(
                {
                    "text": f"edit {g}-{e}",
                    "meanGrade": round(mean, 1),
                    "grades": "".join(str(x) for x in grades),
                    "pair_id": pair,
                    "kind": "edited",
                }
            )
    return rows


def humor_scores(
    rows: Sequence[Mapping[str, Any]], seed: int = 1, signal: float = 0.25
) -> dict[str, float]:
    """A noisy probability that tracks meanGrade; originals score low."""
    rng = np.random.default_rng(seed)
    out: dict[str, float] = {}
    for r in rows:
        grade = r["meanGrade"] if r["meanGrade"] is not None else 0.0
        p = 0.2 + signal * grade / 3 + rng.normal(0, 0.12)
        out[row_key(str(r["text"]))] = float(np.clip(p, 0.001, 0.999))
    return out


MAPPING: dict[str, Any] = {
    "schema": "dw.calibration-mapping/v1",
    "human_label": {
        "column": "meanGrade",
        "rule": "numeric",
        "positive_at_or_above": 1.6,
        "negative_at_or_below": 0.4,
    },
    "ratings": {"column": "grades", "format": "digit_string", "scale": [0, 3]},
    "group": {"column": "pair_id"},
    "strata": ["kind"],
    "reference": {"column": "kind", "value": "original"},
}


def make_run(
    version_id: str,
    scores: Mapping[str, float | None],
    *,
    question: str = QUESTION,
    state: str = "completed",
    model_id: str = "JEV-9B-decision",
    outcomes: Mapping[str, str] | None = None,
    thresholds: tuple[float, float] | None = (0.8, 0.2),
    template_ref: str = "bare-v1",
    pinned: bool = True,
) -> LabelRun:
    identity = labeler_identity(
        protocol="openai_scoring",
        model_id=model_id,
        model_revision="b63f651c",
        template_ref=template_ref,
        question=question,
    )
    with sync_session_factory()() as db:
        run = LabelRun(
            id=f"lr_{db_factories.uid().replace('-', '')[:12]}",
            kind="classifier",
            state=state,
            input_version_id=version_id,
            field_map={"text": "text"},
            endpoint_snapshot={
                "role": "classifier",
                "protocol": "openai_scoring",
                "model_id": model_id,
            },
            question=question,
            positive_label=LABELS[0],
            negative_label=LABELS[1],
            threshold_positive=thresholds[0] if thresholds else None,
            threshold_negative=thresholds[1] if thresholds else None,
            sampling={"temperature": 0},
            chunk_size=200,
            labeler_identity=identity,
            labeler_identity_hash=identity_hash(identity),
            labeler_fingerprint=fingerprint(identity, {"temperature": 0}, "n/a", "single"),
            pinned=pinned,
            revision_reported=True,
            started_by="Test Operator",
            started_by_origin="operator",
        )
        db.add(run)
        db.flush()
        for key, p in scores.items():
            outcome = (outcomes or {}).get(key)
            if outcome is None:
                if p is None:
                    outcome = "skipped"
                elif thresholds and p >= thresholds[0]:
                    outcome = "positive"
                elif thresholds and p <= thresholds[1]:
                    outcome = "negative"
                else:
                    outcome = "excluded"
            db.add(
                Label(
                    label_run_id=run.id,
                    row_key=key,
                    labeler_fingerprint=run.labeler_fingerprint,
                    outcome=outcome,
                    probability=p,
                    parsed_value=p if p is not None else outcome,
                    raw_output={"fixture": True},
                    steering_state="none",
                )
            )
        db.commit()
        db.expunge(run)
        return run


@dataclass
class Runner:
    """Runs a queued calibration job in process through the real task body."""

    emitted: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)

    def emit(self, room: str, event: str, data: dict[str, Any]) -> bool:
        self.emitted.append((room, event, data))
        return True

    def run(self, job_id: str) -> dict[str, Any]:
        from src.workers import calibration_tasks

        return calibration_tasks.run_job(job_id)

    def job(self, job_id: str) -> Job:
        with sync_session_factory()() as db:
            row = db.get(Job, job_id)
            assert row is not None
            db.expunge(row)
            return row


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch, clean_db: None) -> Iterator[Runner]:
    from src.services.calibration import record_service
    from src.workers import calibration_tasks

    r = Runner()
    monkeypatch.setattr(record_service, "_dispatch", lambda: [])
    monkeypatch.setattr(calibration_tasks, "emit", r.emit)
    monkeypatch.setattr(calibration_tasks, "_next_jobs", lambda: None)
    yield r


def qh() -> str:
    return question_hash(QUESTION)


def labels_of(run_id: str) -> list[Label]:
    with sync_session_factory()() as db:
        rows = list(db.execute(select(Label).where(Label.label_run_id == run_id)).scalars())
        for r in rows:
            db.expunge(r)
        return rows
