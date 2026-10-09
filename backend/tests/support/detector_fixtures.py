"""Fixtures for feature 009: the humor detector set's four kinds of role as real versions on disk.

Discipline (FTDD 009 section 10): roles DIFFER in the dimension a test checks — calibration
negatives are headline length and chat negatives are long; the OOD role carries pair groups; a
"shortcut" training version has a metadata column that predicts the label while a clean one does
not. Versions are written by 004's ``make_version`` (real ``_dw_`` columns from 002's row-key
function), so 004's real audit and leakage check run against them.
"""

from __future__ import annotations

import hashlib
from typing import Any

import numpy as np
import pyarrow as pa
from sqlalchemy import text

from src.core.canonical_json import canonical_json
from src.core.database import sync_session_factory
from tests.fixtures.humor_pool import with_system_columns
from tests.support import db_factories
from tests.support.curation_fixtures import make_version

ROLES = {"text": "content", "label": "metadata", "format": "metadata", "pair_id": "metadata"}
WORDS = (
    "senate markets rally storm vote court mayor budget school harbor festival garden "
    "train bridge museum river election council farmers"
).split()
MAPPING = {"humorous": "positive", "not_humorous": "negative"}


def _headline(rng: np.random.Generator, i: int, tag: str) -> str:
    return f"{tag} {i} " + " ".join(rng.choice(WORDS, size=7))


def labelled_rows(
    n: int, *, seed: int, tag: str, shortcut: bool = False, pairs: bool = False
) -> list[dict[str, Any]]:
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        funny = i % 2 == 1
        if shortcut:
            fmt = "joke" if funny else "headline"
        else:
            fmt = "joke" if rng.random() < 0.5 else "headline"
        rows.append(
            {
                "text": _headline(rng, i, tag),
                "label": "humorous" if funny else "not_humorous",
                "format": fmt,
                "pair_id": f"{tag}-g{i // 2}" if pairs else f"{tag}-{i}",
            }
        )
    return rows


def _table(splits: dict[str, list[dict[str, Any]]]) -> pa.Table:
    tables = [with_system_columns(rows, split=name) for name, rows in splits.items()]
    return pa.concat_tables(tables)


def version(
    splits: dict[str, list[dict[str, Any]]],
    *,
    dataset_name: str | None = None,
    held_out: dict[str, bool] | None = None,
    roles: dict[str, str] | None = None,
    steps: list[Any] | None = None,
) -> str:
    """A completed version on disk whose manifest names one Hugging Face source (cc-by-2.0), so
    008's real publish worker accepts it."""
    with sync_session_factory()() as db:
        ds = db_factories.dataset(db, name=dataset_name)
        db.commit()
        v = make_version(
            db, _table(splits), roles or ROLES, split_roles=held_out, dataset=ds, steps=steps
        )
        src = db_factories.source(db, repo_id=f"org/{ds.name}")
        db.commit()
        manifest = canonical_json(
            {"format": "detector-fixture", "id": str(v.id), "sources": [{"source_id": src.id}]}
        )
        db.execute(text("ALTER TABLE dw_versions DISABLE TRIGGER dw_versions_immutable"))
        db.execute(text("ALTER TABLE dw_versions DISABLE TRIGGER dw_versions_manifest_hash"))
        v.manifest = manifest
        v.manifest_sha256 = hashlib.sha256(manifest).hexdigest()
        db.commit()
        db.execute(text("ALTER TABLE dw_versions ENABLE TRIGGER dw_versions_immutable"))
        db.execute(text("ALTER TABLE dw_versions ENABLE TRIGGER dw_versions_manifest_hash"))
        db.commit()
        return str(v.id)


def chat_rows(n: int, *, seed: int) -> list[dict[str, Any]]:
    rng = np.random.default_rng(seed)
    return [
        {
            "text": " ".join(rng.choice(WORDS, size=int(rng.integers(180, 420)))),
            "label": "not_humorous",
            "format": "chat",
            "pair_id": f"chat-{i}",
        }
        for i in range(n)
    ]


def headline_negatives(n: int, *, seed: int) -> list[dict[str, Any]]:
    rng = np.random.default_rng(seed)
    return [
        {
            "text": _headline(rng, i, "cal"),
            "label": "not_humorous",
            "format": "headline",
            "pair_id": f"cal-{i}",
        }
        for i in range(n)
    ]


def humor_versions(*, shortcut: bool = False, chat_calibration: bool = False) -> dict[str, str]:
    """train/test version, OOD version (paired), calibration version."""
    train = version(
        {
            "train": labelled_rows(200, seed=1, tag="tr", shortcut=shortcut),
            "test": labelled_rows(60, seed=2, tag="te", shortcut=shortcut),
        },
        dataset_name="humor-balanced",
        held_out={"test": True},
    )
    ood = version(
        {"test": labelled_rows(120, seed=3, tag="ood", pairs=True)}, dataset_name="humicroedit"
    )
    cal_rows = chat_rows(80, seed=4) if chat_calibration else headline_negatives(150, seed=5)
    cal = version({"train": cal_rows}, dataset_name="headline-negatives")
    return {"train": train, "ood": ood, "cal": cal}


def set_body(v: dict[str, str], name: str = "humor-set", **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name,
        "description": "Humor in headlines",
        "positive_meaning": "Most readers would find the text funny",
        "roles": [
            {
                "role": "train",
                "version_id": v["train"],
                "split": "train",
                "input_column": "text",
                "label_column": "label",
                "label_mapping": MAPPING,
            },
            {
                "role": "id_test",
                "version_id": v["train"],
                "split": "test",
                "input_column": "text",
                "label_column": "label",
                "label_mapping": MAPPING,
            },
            {
                "role": "ood_eval",
                "version_id": v["ood"],
                "split": "test",
                "input_column": "text",
                "label_column": "label",
                "label_mapping": MAPPING,
                "pair_column": "pair_id",
            },
            {
                "role": "calibration_negatives",
                "version_id": v["cal"],
                "split": "train",
                "input_column": "text",
                "label_column": "label",
                "label_mapping": {"not_humorous": "negative"},
                "negatives_basis": {
                    "kind": "labeler_filtered",
                    "labeler_identity_hash": "a" * 64,
                    "rule": "JEV P <= 0.20",
                },
            },
        ],
    }
    body.update(over)
    return body


# --- Humicroedit, in its REAL shape (production defects of 2026-10-07) -------------------------
#
# The production set dts_9bc3601e674ad12d49469598 bound one Humicroedit version as all three roles.
# Its shape, measured on the live version's rows (train / test / validation):
#   - ``human_label`` is DEFINED from ``meanGrade``: 1 at >= 1.6, 0 at <= 0.4 or an unedited
#     original, and NULL in the middle band (train 5,392 of 12,962 rows; test 1,714 of 4,055);
#   - ``meanGrade`` is NULL on originals and ``grades`` is "" on originals;
#   - edited negatives outnumber edited positives (2,553 to 1,707 in train), so ``kind`` alone is
#     not a shortcut, while ``meanGrade`` (1.000) and ``grades`` (0.994) are, by construction.
# Every fixture below keeps those properties; a fixture whose label column has no nulls is what let
# D-2 pass a mapping that forgot ``None``.

HUMICROEDIT_ROLES = {
    "id": "metadata",
    "pair_id": "metadata",
    "kind": "metadata",
    "text": "content",
    "meanGrade": "metadata",
    "grades": "metadata",
    "n_graders": "metadata",
    "human_label": "metadata",
}
HUMICROEDIT_MAPPING = {"1": "positive", "0": "negative", "None": "excluded"}
HUMICROEDIT_CALIBRATION_MAPPING = {"0": "negative", "1": "excluded", "None": "excluded"}
#: The basis production had to misstate as ``assumed_negative`` (FR-009.6 had no human kind).
HUMAN_BASIS = {
    "kind": "human_labelled",
    "label_column": "human_label",
    "negative_values": ["0"],
    "labelled_by": "five Humicroedit graders per edited headline (SemEval-2020 Task 7)",
    "rule": "meanGrade <= 0.4, or an unedited original headline",
}

#: Rows per split: (originals, edited funny, edited unfunny, edited middle band).
HUMICROEDIT_SHAPE = {
    "train": (66, 34, 51, 108),
    "test": (40, 22, 31, 69),
    "validation": (33, 17, 25, 56),
}


def _grades(rng: np.random.Generator, low: int, high: int) -> tuple[str, float]:
    marks = sorted((int(x) for x in rng.integers(low, high + 1, size=5)), reverse=True)
    return "".join(str(m) for m in marks), round(sum(marks) / 5, 1)


def humicroedit_rows(split: str, *, seed: int) -> list[dict[str, Any]]:
    originals, funny, unfunny, middle = HUMICROEDIT_SHAPE[split]
    rng = np.random.default_rng(seed)
    rows: list[dict[str, Any]] = []
    i = 0

    def add(kind: str, grades: str, mean: float | None, label: int | None) -> None:
        nonlocal i
        rows.append(
            {
                "id": f"hum-{split}-{i}",
                "pair_id": "",
                "kind": kind,
                "text": _headline(rng, i, f"hum{split}"),
                "meanGrade": mean,
                "grades": grades,
                "n_graders": 5 if kind == "edited" else 0,
                "human_label": label,
            }
        )
        i += 1

    for _ in range(originals):
        add("original", "", None, 0)
    for _ in range(funny):
        g, m = _grades(rng, 1, 3)
        add("edited", g, max(m, 1.6), 1)
    for _ in range(unfunny):
        g, m = _grades(rng, 0, 1)
        add("edited", g, min(m, 0.4), 0)
    for _ in range(middle):
        g, m = _grades(rng, 0, 2)
        add("edited", g, min(max(m, 0.6), 1.4), None)
    order = rng.permutation(len(rows))
    shuffled = [rows[int(k)] for k in order]
    # pair groups (an original and its edits) are assigned AFTER shuffling, so a group mixes
    # labels as Humicroedit's do and ``pair_id`` predicts nothing
    for position, row in enumerate(shuffled):
        row["pair_id"] = f"{split}-p{position // 3}"
    return shuffled


def humicroedit_version(dataset_name: str = "humicroedit") -> str:
    """One version with train, test and validation splits, as production bound it."""
    return version(
        {s: humicroedit_rows(s, seed=40 + n) for n, s in enumerate(HUMICROEDIT_SHAPE)},
        dataset_name=dataset_name,
        held_out={"test": True, "validation": True},
        roles=HUMICROEDIT_ROLES,
    )


def humicroedit_body(
    version_id: str,
    *,
    name: str = "humicroedit-funny",
    label_sources: list[str] | None = None,
    basis: dict[str, Any] | None = None,
    ood_version: str | None = None,
) -> dict[str, Any]:
    """The production set's body (set_create.json), plus an OOD role and a monitored text so D-1
    passes, with ``label_source_columns`` on the audited roles when given."""

    def role(kind: str, split: str, mapping: dict[str, str], **extra: Any) -> dict[str, Any]:
        out: dict[str, Any] = {
            "role": kind,
            "version_id": version_id,
            "split": split,
            "input_column": "text",
            "label_column": "human_label",
            "label_mapping": mapping,
        }
        out.update(extra)
        return out

    sources = {"label_source_columns": label_sources} if label_sources is not None else {}
    roles = [
        role("train", "train", HUMICROEDIT_MAPPING, **sources),
        role("id_test", "test", HUMICROEDIT_MAPPING, **sources),
        role(
            "calibration_negatives",
            "validation",
            HUMICROEDIT_CALIBRATION_MAPPING,
            negatives_basis=basis or {"kind": "assumed_negative"},
        ),
    ]
    if ood_version is not None:
        roles.append(
            {
                "role": "ood_eval",
                "version_id": ood_version,
                "split": "test",
                "input_column": "text",
                "label_column": "label",
                "label_mapping": MAPPING,
                "pair_column": "pair_id",
            }
        )
    return {
        "name": name,
        "description": "Humicroedit funny / not funny on human labels",
        "positive_meaning": "humorous (Humicroedit human_label = 1)",
        "roles": roles,
    }
