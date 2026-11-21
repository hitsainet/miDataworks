"""Map a version and its build to ``midataworks.dataset-version/v1`` (FR-008.4, FR-008.29,
FR-008.35–FR-008.37; FTID 008 section 7.2).

Order, always: build the pydantic document → dump it → validate the dump against the COMMITTED
schema file with ``Draft202012Validator`` → serialise with the one canonical-JSON function →
hash those bytes. A document that fails the file is never written (FTASKS 8.4).

Nothing here carries a name, an endpoint URL or a key (FPRD 008 section 5.2): ``who`` is origin
only, model identity is model ID and revision, and sources are Hub IDs or upload hashes.
"""

from __future__ import annotations

import copy
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


def _fill_added(data: Mapping[str, Any]) -> dict[str, Any]:
    """A deep copy with the two keys added within v1 present: ``null`` where a document lacks
    them: a manifest stored before 021 (or by its push 1, which emitted neither key), read back
    by :func:`with_publication`. Every writer since passes both (021 FTID I2, push 3)."""
    out: dict[str, Any] = copy.deepcopy(dict(data))
    out.setdefault("sensitivity", None)
    content = out.get("content")
    if isinstance(content, dict) and content.get("content_kind") == "rows":
        for split in content.get("splits") or []:
            if isinstance(split, dict):
                split.setdefault("wsp", None)
    return out


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
    sensitivity: str | None,
    sensitivity_basis: Mapping[str, Any],
    generated_at: datetime | None = None,
    extensions: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The manifest as validated JSON-ready data. Raises on any model or file violation.

    ``sensitivity`` and ``sensitivity_basis`` are REQUIRED keywords (021 FR-021.21): every site
    passes ``services.sensitivity.effective(session, version)``, so no manifest can forget the
    value or state the stored floor where lineage has raised it; the basis lands in
    ``extensions.sensitivity_basis``.

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
            "sensitivity": sensitivity,
            "publication": dict(publication) if publication is not None else None,
            "extensions": {**dict(extensions or {}), "sensitivity_basis": dict(sensitivity_basis)},
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
    """The same facts in another publication state (built → in_repository → published).

    The document is returned AS GIVEN (only ``publication`` changes): a stored manifest written
    before 021, or while push 1 is live, lacks the two keys added within v1, so the model sees a
    filled copy (R1A-D1: without it every push-1 publish failed after its files reached the Hub);
    the file then checks both the copy and the document this returns, which is what the publish
    job writes (R2B-24)."""
    data: dict[str, Any] = json.loads(json.dumps(dict(document)))
    data["publication"] = dict(publication) if publication is not None else None
    filled = _fill_added(data)
    DatasetVersionManifest.model_validate(filled)
    validate_against_file(filled)
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
    split_extensions: Mapping[str, Mapping[str, Any]] | None = None,
    version_marks: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """``split_extensions``: split name -> that split's ``extensions`` (017: the held-out proof).

    ``version_marks``: the version's WSP marks (``wsp_partitions.declared_on``). Each split copies
    its build file's ``wsp`` (``wsp_partitions.shipped_wsp`` wrote it). A version WITH marks whose
    build file has no ``wsp`` key at all was built before 021 and is refused
    ``build_predates_wsp``: a defensive guard production does not reach today (R1A-D17)."""
    extra = split_extensions or {}
    if version_marks:
        stale = [f["name"] for f in build_files if "wsp" not in f]
        if stale:
            from ...core.errors import AppError

            raise AppError(
                f"The build of this version predates WSP partitions (splits {stale}), so its "
                "files carry no marks. Build the files again, then publish.",
                code="build_predates_wsp",
                status_code=409,
                details={"splits": stale},
            )
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
                "wsp": f["wsp"] if "wsp" in f else None,
                "extensions": dict(extra.get(f["name"]) or {}),
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


def miforge_set_target(
    target_type: str, set_kind: str, dataset_name: str, version_number: int
) -> dict[str, Any]:
    """The manifest target of a miForge set (FR-008.41, FR-008.43, FR-008.68), as the export
    writes it: the set kind and a contract reference naming the dataset and version."""
    return {
        "kind": "miforge_set",
        "dataset_target_type": target_type,
        "trl_type": None,
        "trl_version": None,
        "detector_role": None,
        "miforge_set_kind": set_kind,
        "contract_reference": {
            "type": set_kind,
            "name": dataset_name,
            "version": f"v{version_number}",
        },
    }


def publish_target(pub: Any, target_type: str, dataset_name: str, version: Any) -> dict[str, Any]:
    """A publish's manifest target from its stored ``target_kind`` (D7)."""
    if pub.target_kind == "miforge_set":
        return miforge_set_target(target_type, pub.miforge_set_kind, dataset_name, version.number)
    return dataset_target(target_type)


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
