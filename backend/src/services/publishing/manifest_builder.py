"""Map a version and its build to ``midataworks.dataset-version/v1`` (FR-008.4, FR-008.29,
FR-008.35–FR-008.37; FTID 008 section 7.2).

Order, always: build the pydantic document → dump it → validate the dump against the COMMITTED
schema file with ``Draft202012Validator`` → serialise with the one canonical-JSON function →
hash those bytes. A document that fails the file is never written (FTASKS 8.4).

Nothing here carries a name, an endpoint URL or a key (FPRD 008 section 5.2): ``who`` is origin
only, model identity is model ID and revision, and sources are Hub IDs or upload hashes.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator

from ...core.canonical_json import canonical_json
from ...core.config import get_settings
from ...schemas.dataset_version import PACKAGED_SCHEMA_PATH, DatasetVersionManifest
from ..identity import bytes_sha256

MANIFEST_FILE = "midataworks-dataset-version.json"


class ManifestInvalid(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(
            "the manifest does not validate against the committed schema: " + "; ".join(errors[:5])
        )
        self.errors = errors


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(PACKAGED_SCHEMA_PATH.read_bytes()))


def validate_against_file(document: bytes | Mapping[str, Any]) -> None:
    """Refuse a document the committed schema file refuses (FR-008.35)."""
    data = json.loads(document) if isinstance(document, bytes) else dict(document)
    errors = sorted(_validator().iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        raise ManifestInvalid(
            [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in errors]
        )


def timestamp(value: datetime | None = None) -> str:
    """RFC 3339 UTC with a ``Z``, microseconds kept: the contract's Timestamp."""
    moment = (value or datetime.now(UTC)).astimezone(UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def producer(generated_at: datetime | None = None) -> dict[str, Any]:
    return {
        "app": "miDataworks",
        "build": get_settings().app_build,
        "generated_at": timestamp(generated_at),
    }


def build_document(
    *,
    version: Mapping[str, Any],
    target: Mapping[str, Any],
    sources: Sequence[Mapping[str, Any]],
    content: Mapping[str, Any],
    caveats: Sequence[Mapping[str, Any]],
    publication: Mapping[str, Any] | None,
    generated_at: datetime | None = None,
    extensions: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The manifest as validated JSON-ready data. Raises on any model or file violation.

    ``extensions`` carries additive facts the v1 fields do not name (the schema's ``extensions``
    is open): 008 writes ``lineage`` — ancestor versions, generation runs and the generated row
    count — so the card's record section is rendered from the document alone."""
    doc = DatasetVersionManifest.model_validate(
        {
            "kind": "midataworks.dataset-version",
            "schema_version": "1",
            "producer": producer(generated_at),
            "version": dict(version),
            "target": dict(target),
            "sources": [dict(s) for s in sources],
            "content": dict(content),
            "caveats": [dict(c) for c in caveats],
            "publication": dict(publication) if publication is not None else None,
            "extensions": dict(extensions or {}),
        }
    )
    data: dict[str, Any] = doc.model_dump(mode="json")
    validate_against_file(data)
    return data


def manifest_bytes(document: Mapping[str, Any]) -> bytes:
    """Validated, then serialised once by the canonical function. The bytes written are hashed."""
    validate_against_file(document)
    return canonical_json(dict(document))


def manifest_digest(content: bytes) -> str:
    return bytes_sha256(content)


def with_publication(
    document: Mapping[str, Any], publication: Mapping[str, Any] | None
) -> dict[str, Any]:
    """The same facts in another publication state (built → in_repository → published)."""
    data: dict[str, Any] = json.loads(json.dumps(dict(document)))
    data["publication"] = dict(publication) if publication is not None else None
    DatasetVersionManifest.model_validate(data)
    validate_against_file(data)
    return data


def rows_content(
    *,
    build_files: Sequence[Mapping[str, Any]],
    columns: Sequence[Mapping[str, Any]],
    label_column: str | None,
    omitted: Mapping[str, Any],
    labelers: Sequence[Mapping[str, Any]],
    calibration: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "content_kind": "rows",
        "columns": [dict(c) for c in columns],
        "splits": [
            {
                "name": f["name"],
                "path": f["path"],
                "rows": f["rows"],
                "bytes": f["bytes"],
                "sha256": f["sha256"],
                "logical_digest": f["logical_digest"],
                "label_counts": dict(f["label_counts"]),
                "held_out": f["held_out"],
                "evaluation_only": f["evaluation_only"],
                "extensions": {},
            }
            for f in build_files
        ],
        "projection": {
            "label_resolver": "dw.effective-label/v1",
            "label_column": label_column,
            "omitted_excluded": int(omitted["excluded"]),
            "omitted_flagged_unresolved": int(omitted["flagged_unresolved"]),
            "overrides_applied": int(omitted["overrides_applied"]),
        },
        "labelers": [dict(x) for x in labelers],
        "calibration": [dict(x) for x in calibration],
    }


def dataset_target(target_type: str) -> dict[str, Any]:
    return {
        "kind": "dataset",
        "dataset_target_type": target_type,
        "trl_type": None,
        "trl_version": None,
        "detector_role": None,
        "miforge_set_kind": None,
        "contract_reference": None,
    }
