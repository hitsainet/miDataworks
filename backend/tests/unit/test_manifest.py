"""The manifest's bytes are pinned, and it carries no secret field (task 9.5)."""

from __future__ import annotations

import hashlib

from src.services.manifest import FIELDS, build_manifest, manifest_bytes

ARGS: dict[str, object] = dict(  # noqa: C408 - keyword arguments, as build_manifest takes them
    version_id="v1",
    dataset={"id": "d1", "name": "humor", "target_type": "detector"},
    number=1,
    inputs=[{"kind": "source", "source_id": "s1", "revision": "2bb7d6bc"}],
    parent_version_id=None,
    recipe={"hash": "h", "revision_id": "r1", "body": {"format": "dw.recipe/v1", "steps": []}},
    seed=20261005,
    bindings=[],
    rowkey_scheme="dw.rowkey/v1",
    column_roles={"text": "content"},
    splits=[
        {
            "name": "train",
            "held_out": False,
            "rows": 2,
            "bytes": 10,
            "file_sha256": "f",
            "logical_digest": "l",
            "path": "versions/v1/train.parquet",
        }
    ],
    held_out_origin_version_id=None,
    steps=[],
    drop_summary=[],
    sources=[{"source_id": "s1", "licence_display": "cc-by-2.0"}],
    warnings=[],
    build_job_id="job_1",
    created_by="Ada",
    created_by_origin="operator",
)

PINNED = (
    b'{"bindings":[],"build_job_id":"job_1","column_roles":{"text":"content"},"created_by":"Ada",'
    b'"created_by_origin":"operator","dataset":{"id":"d1","name":"humor","target_type":"detector"},'
    b'"drop_summary":[],"format":"dw.version-manifest/v1","held_out_origin_version_id":null,'
    b'"inputs":[{"kind":"source","revision":"2bb7d6bc","source_id":"s1"}],"number":1,'
    b'"parent_version_id":null,"recipe":{"body":{"format":"dw.recipe/v1","steps":[]},"hash":"h",'
    b'"revision_id":"r1"},"rowkey_scheme":"dw.rowkey/v1","seed":20261005,"sources":[{'
    b'"licence_display":"cc-by-2.0","source_id":"s1"}],"splits":[{"bytes":10,"file_sha256":"f",'
    b'"held_out":false,"logical_digest":"l","name":"train","path":"versions/v1/train.parquet",'
    b'"rows":2}],"steps":[],"total_bytes":10,"total_rows":2,"version_id":"v1","warnings":[]}'
)


def test_the_manifest_bytes_are_pinned() -> None:
    content = manifest_bytes(build_manifest(**ARGS))  # type: ignore[arg-type]
    assert content == PINNED
    assert hashlib.sha256(content).hexdigest() == hashlib.sha256(PINNED).hexdigest()


def test_no_field_could_carry_a_secret() -> None:
    forbidden = ("api_key", "token", "secret", "password", "authorization", "endpoint")
    assert not [f for f in FIELDS if any(word in f for word in forbidden)]
    document = build_manifest(**ARGS)  # type: ignore[arg-type]
    assert tuple(document) == FIELDS
