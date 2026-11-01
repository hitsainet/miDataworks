"""The version manifest (FR-002.9; C-002.7): one canonical JSON document per version.

Built here, serialised ONCE by the single canonical-JSON function, written to
``versions/<id>/manifest.json`` and stored in ``dw_versions.manifest`` as the same bytes; the API
serves the stored bytes unchanged with ``ETag: <manifest_sha256>``. Feature 008 maps this document
to ``midataworks.dataset-version/v1``.

It never carries a secret: bindings are run IDs, sources carry licences and pins, and nothing from
an endpoint configuration reaches it (``tests/unit/test_manifest.py`` asserts the field set).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..core.canonical_json import canonical_json

MANIFEST_FORMAT = "dw.version-manifest/v1"

#: Every top-level field, in one place, so a test can pin the set (and so no field arrives by
#: accident — an endpoint key in a manifest would be published).
FIELDS: tuple[str, ...] = (
    "format",
    "version_id",
    "dataset",
    "number",
    "inputs",
    "parent_version_id",
    "recipe",
    "seed",
    "bindings",
    "rowkey_scheme",
    "column_roles",
    "splits",
    "total_rows",
    "total_bytes",
    "held_out_origin_version_id",
    "steps",
    "drop_summary",
    "sources",
    "warnings",
    "build_job_id",
    "created_by",
    "created_by_origin",
)


def build_manifest(
    *,
    version_id: str,
    dataset: Mapping[str, Any],
    number: int,
    inputs: Sequence[Mapping[str, Any]],
    parent_version_id: str | None,
    recipe: Mapping[str, Any],
    seed: int,
    bindings: Sequence[Mapping[str, Any]],
    rowkey_scheme: str,
    column_roles: Mapping[str, str],
    splits: Sequence[Mapping[str, Any]],
    held_out_origin_version_id: str | None,
    steps: Sequence[Mapping[str, Any]],
    drop_summary: Sequence[Mapping[str, Any]],
    sources: Sequence[Mapping[str, Any]],
    warnings: Sequence[Mapping[str, Any]],
    build_job_id: str,
    created_by: str,
    created_by_origin: str,
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "format": MANIFEST_FORMAT,
        "version_id": version_id,
        "dataset": dict(dataset),
        "number": number,
        "inputs": [dict(i) for i in inputs],
        "parent_version_id": parent_version_id,
        "recipe": dict(recipe),
        "seed": seed,
        "bindings": [dict(b) for b in bindings],
        "rowkey_scheme": rowkey_scheme,
        "column_roles": dict(column_roles),
        "splits": [dict(s) for s in splits],
        "total_rows": sum(int(s["rows"]) for s in splits),
        "total_bytes": sum(int(s["bytes"]) for s in splits),
        "held_out_origin_version_id": held_out_origin_version_id,
        "steps": [dict(s) for s in steps],
        "drop_summary": [dict(d) for d in drop_summary],
        "sources": [dict(s) for s in sources],
        "warnings": [dict(w) for w in warnings],
        "build_job_id": build_job_id,
        "created_by": created_by,
        "created_by_origin": created_by_origin,
    }
    assert tuple(document) == FIELDS
    return document


def manifest_bytes(document: Mapping[str, Any]) -> bytes:
    """The bytes written AND hashed AND stored. There is no second serialiser."""
    return canonical_json(dict(document))
