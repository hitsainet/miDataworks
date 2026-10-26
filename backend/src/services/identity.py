"""Content addresses: recipe hash, step seed, step identity, request digest, logical digest.

FR-002.5, FR-002.11, FR-002.28, FR-002.30, FR-002.34; FTDD 002 sections 4.5 and 4.6.

One function per fact, and each hashes bytes from the single canonical-JSON function (ADR-005), so
a hash always describes bytes that could be written. ``tests/unit/test_single_function_call_sites.py``
holds every ``hashlib.sha256`` call in ``services/`` and ``workers/`` to this module and
``row_keys.py``.

**Step identity input (a deliberate departure, recorded in the implementation-controls review).**
FTDD section 4.5 keys a step on its input's *logical* digest, which covers only the ordered
(row key, occurrence) pairs. A step that changes only a metadata column keeps every key
(FR-002.20), so the next step would see an unchanged logical digest and reuse output carrying the
OLD metadata — stale output, silently. The identity therefore takes the identity digest of the
execution that PRODUCED its input (a chain: equal chains mean equal inputs). The logical digest is
still recorded on every execution and is what verify rebuild compares (FR-002.34).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..core.canonical_json import canonical_json

#: The version seed's exclusive upper bound (FTDD section 4.5; ``random_state`` friendly).
VERSION_SEED_LIMIT = 2**31


def _sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def recipe_hash(body: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical bytes of a recipe body (FR-002.11).

    Name, description and step labels live outside the body, so they never reach the hash.
    """
    return hashlib.sha256(canonical_body(body)).hexdigest()


def canonical_body(body: Mapping[str, Any]) -> bytes:
    """The exact bytes a recipe body is hashed and stored as."""
    return canonical_json(dict(body))


def params_hash(params: Mapping[str, Any]) -> str:
    return _sha256(dict(params))


def operator_ref(name: str, version: str) -> str:
    return f"{name}@{version}"


def step_seed(seed: int, index: int, ref: str) -> int:
    """A 32-bit unsigned seed for step ``index``, derived from the version seed (FR-002.30)."""
    digest = hashlib.sha256(canonical_json({"seed": seed, "step": index, "operator": ref})).digest()
    return int.from_bytes(digest[:4], "big")


def response_seed(run_seed: int, prompt_key: str, response_index: int) -> int:
    """Feature 007's per-response seed (FTID 007 section 7.1): the first 4 bytes of SHA-256 over
    the canonical JSON ``[run_seed, prompt_key, response_index]``, below 2**31 (miLLM's seed
    bound). The same in every process (never Python's ``hash()``)."""
    digest = hashlib.sha256(
        canonical_json([int(run_seed), str(prompt_key), int(response_index)])
    ).digest()
    return int.from_bytes(digest[:4], "big") % 2_147_483_648


def bindings_digest(bindings: Sequence[Mapping[str, Any]]) -> str:
    """Digest of the bindings a step consumes, order-independent (sorted by kind then id)."""
    ordered = sorted(({"kind": b["kind"], "id": str(b["id"])} for b in bindings), key=_binding_key)
    return _sha256(ordered)


def _binding_key(binding: Mapping[str, str]) -> tuple[str, str]:
    return binding["kind"], binding["id"]


def step_identity(
    *,
    input_digest: str,
    kind: str,
    ref: str,
    manifest_hash: str,
    params_digest: str,
    seed: int,
    bindings: str,
    rowkey_scheme: str,
) -> str:
    """The identity of one step execution (FR-002.28). Every field matters; a mutation control
    drops each in turn and requires a red."""
    return _sha256(
        {
            "input": input_digest,
            "kind": kind,
            "operator": ref,
            "manifest": manifest_hash,
            "params": params_digest,
            "seed": seed,
            "bindings": bindings,
            "rowkey": rowkey_scheme,
        }
    )


def assemble_identity(
    inputs: Sequence[Mapping[str, Any]], roles: Mapping[str, str], rowkey_scheme: str
) -> str:
    """Identity of the assemble step (index 0): resolved inputs in order, initial roles, scheme."""
    return _sha256({"inputs": list(inputs), "roles": dict(roles), "rowkey": rowkey_scheme})


def request_digest(
    *,
    dataset_id: str,
    inputs: Sequence[Mapping[str, Any]],
    recipe_hash_hex: str,
    seed: int,
    bindings: Sequence[Mapping[str, Any]],
    rowkey_scheme: str,
    column_role_overrides: Mapping[str, str],
) -> str:
    """The build request's identity (FR-002.5). Inputs keep request order: concatenation order is
    part of the output."""
    return _sha256(
        {
            "dataset_id": dataset_id,
            "inputs": list(inputs),
            "recipe_hash": recipe_hash_hex,
            "seed": seed,
            "bindings": sorted(
                ({"kind": b["kind"], "id": str(b["id"])} for b in bindings), key=_binding_key
            ),
            "rowkey_scheme": rowkey_scheme,
            "column_role_overrides": dict(column_role_overrides),
        }
    )


def logical_digest_of_pairs(pairs: Sequence[tuple[str, int]]) -> str:
    """SHA-256 over ``"<key>:<occurrence>\\n"`` lines in the given order (FR-002.34)."""
    digest = hashlib.sha256()
    for key, occurrence in pairs:
        digest.update(f"{key}:{occurrence}\n".encode())
    return digest.hexdigest()


def logical_digest(paths: Path | Sequence[Path]) -> str:
    """Streamed logical digest of one or more Parquet files, in file then row order."""
    import pyarrow.parquet as pq

    files = [paths] if isinstance(paths, Path) else list(paths)
    digest = hashlib.sha256()
    for path in files:
        handle = pq.ParquetFile(path)
        for batch in handle.iter_batches(columns=["_dw_row_key", "_dw_occurrence"]):
            keys = batch.column(0).to_pylist()
            occurrences = batch.column(1).to_pylist()
            digest.update(
                "".join(f"{k}:{o}\n" for k, o in zip(keys, occurrences, strict=True)).encode()
            )
    return digest.hexdigest()


def bytes_sha256(content: bytes) -> str:
    """SHA-256 of bytes already in memory (a manifest the caller just serialised)."""
    return hashlib.sha256(content).hexdigest()


def new_hasher() -> Any:
    """An incremental SHA-256 for bytes that arrive in chunks (an upload being received)."""
    return hashlib.sha256()


def file_sha256(path: Path, chunk: int = 1 << 20) -> str:
    """SHA-256 of a file's bytes, read from disk in chunks (hash what was written)."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def digest_text(value: Any) -> str:
    """Canonical SHA-256 of any JSON value (manifests, approval facts, comparisons)."""
    return _sha256(value)
