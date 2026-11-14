"""A valid ``midataworks.dataset-version/v1`` document, built from the prototype's records.

The counts come from ``records/format_balanced.json`` (2,208 / 244, 552 and 61 per cell) so the
fixture is a real version's shape, not a minimal one that agrees with the schema by construction.
Each caller gets a deep copy to break.
"""

from __future__ import annotations

import copy
from typing import Any

SHA_A = "a" * 64
SHA_B = "b" * 64
COMMIT = "c" * 40


def rows_manifest() -> dict[str, Any]:
    doc: dict[str, Any] = {
        "kind": "midataworks.dataset-version",
        "schema_version": "1",
        "producer": {"app": "miDataworks", "build": "dev", "generated_at": "2026-10-07T12:00:00Z"},
        "version": {
            "dataset": "humor-format-balanced",
            "version_id": "7b5d1d0e-0000-4000-8000-000000000001",
            "number": 2,
            "parent_version_id": None,
            "created_at": "2026-10-05T09:30:00Z",
            "created_by_origin": "operator",
            "recipe": {"format": "dw.recipe/v1", "sha256": SHA_A},
            "seed": 20261005,
            "row_key_scheme": "dw.rowkey/v1",
        },
        "target": {
            "kind": "dataset",
            "dataset_target_type": "detector",
            "trl_type": None,
            "trl_version": None,
            "detector_role": None,
            "miforge_set_kind": None,
            "contract_reference": None,
        },
        "sources": [
            {
                "source_id": "src-colbert",
                "origin": {
                    "kind": "hub",
                    "repo_id": "CreativeLang/ColBERT_Humor_Detection",
                    "config": None,
                    "split": None,
                    "revision": "2bb7d6bce15e42c2a3cf2be8305fa3049929d3ac",
                },
                "licence": {
                    "displayed": "cc-by-2.0",
                    "raw": "cc-by-2.0",
                    "origin": "card_data",
                    "licence_class": "permits_redistribution",
                    "table_version": 1,
                },
                "terms_status": "not_recorded",
                "gated": False,
                "extensions": {},
            }
        ],
        "content": {
            "content_kind": "rows",
            "columns": [
                {
                    "name": "text",
                    "arrow_type": "string",
                    "role": "content",
                    "semantic": "text",
                    "label_values": None,
                    "extensions": {},
                },
                {
                    "name": "label",
                    "arrow_type": "string",
                    "role": "metadata",
                    "semantic": "label",
                    "label_values": ["humorous", "not_humorous"],
                    "extensions": {},
                },
            ],
            "splits": [
                {
                    "name": "train",
                    "path": "data/train.parquet",
                    "rows": 2208,
                    "bytes": 181234,
                    "sha256": SHA_A,
                    "logical_digest": SHA_B,
                    "label_counts": {"humorous": 1104, "not_humorous": 1104},
                    "held_out": False,
                    "evaluation_only": False,
                    "extensions": {},
                },
                {
                    "name": "test",
                    "path": "data/test.parquet",
                    "rows": 244,
                    "bytes": 20871,
                    "sha256": SHA_B,
                    "logical_digest": SHA_A,
                    "label_counts": {"humorous": 122, "not_humorous": 122},
                    "held_out": True,
                    "evaluation_only": False,
                    "extensions": {},
                },
            ],
            "projection": {
                "label_resolver": "dw.effective-label/v1",
                "label_column": "label",
                "omitted_excluded": 0,
                "omitted_flagged_unresolved": 0,
                "overrides_applied": 0,
            },
            "labelers": [],
            "calibration": [],
        },
        "caveats": [
            {
                "code": "amber_check",
                "severity": "amber",
                "message": "C-5: no completed audit sample is recorded for this version.",
                "detail": {"check": "C-5"},
            },
        ],
        "publication": None,
        "extensions": {},
    }
    return copy.deepcopy(doc)


def published(doc: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(doc)
    out["publication"] = {
        "state": "published",
        "repo_id": "mistudio/humor-format-balanced",
        "repo_type": "dataset",
        "visibility": "private",
        "commit": COMMIT,
        "published_at": "2026-10-07T12:05:00Z",
        "verification": {
            "result": "hashes_match",
            "files_checked": 4,
            "checked_at": "2026-10-07T12:05:01Z",
        },
    }
    return out
