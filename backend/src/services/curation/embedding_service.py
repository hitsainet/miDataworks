"""Embeddings for curation (004 FR-004.14, FR-004.24 embedding basis; P-18; FTASKS 15.1, 15.3).

One way to get a row's vector: :func:`embed`, called by an operator running on the ``labeling``
queue with the ``embeddings`` endpoint role (feature 005's resolver, ``ctx.endpoint``) and the
shared miLLM lease the executor took (``ctx.lease_id``, P-05). Every request goes through 005's
``EndpointCaller`` with ``purpose="scoring"``: the strict header, ``X-miLLM-Load-Policy: refuse``
(miDataworks never loads a model), the lease header when one is held, and backpressure handled
there (503 with ``Retry-After``).

- **Never truncated.** A row whose text is longer than ``CURATION_EMBED_MAX_INPUT_CHARS`` is not
  sent; it is returned as skipped with reason ``over_input_cap`` and compared with nothing.
- **Never empty.** A row whose text is empty or whitespace only is not sent either (the endpoint
  refuses it and the batch fails); it is skipped with reason ``empty_input``.
- **Cached** by (row key, specification hash) as Parquet under ``cache/embeddings/<spec_hash>/``
  (ADR-004 amendment): the row key is the hash of the row's content, so a cached vector is the
  vector of that exact text. The specification (server origin, model, input form, cap) is recorded
  in the operator's report; the key is never written anywhere (ADR-015).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from ...clients.endpoint_caller import EndpointCaller, server_origin
from ...clients.endpoint_errors import EndpointCallError
from ...core.canonical_json import canonical_sha256
from ...core.config import get_settings
from ...core.storage import atomic_write, embeddings_cache_dir
from ...operators.errors import OperatorError

EMBED_PATH = "/v1/embeddings"
INPUT_FORM = "content_columns_joined_newline_v1"
OVER_INPUT_CAP = "over_input_cap"
#: An empty or whitespace-only text is never sent: embeddings endpoints refuse it with a 400 that
#: fails the whole batch (production, 2026-10-17). It is skipped and compared with nothing.
EMPTY_INPUT = "empty_input"


@dataclass(frozen=True)
class EmbeddingSpec:
    origin: str
    model: str
    max_input_chars: int
    input_form: str = INPUT_FORM

    def as_dict(self) -> dict[str, Any]:
        return {
            "origin": self.origin,
            "model": self.model,
            "max_input_chars": self.max_input_chars,
            "input_form": self.input_form,
        }

    @property
    def spec_hash(self) -> str:
        return canonical_sha256(self.as_dict())


@dataclass
class Embedded:
    """Vectors for the rows that were embedded; ``skipped`` maps a row index to its reason."""

    spec: EmbeddingSpec
    vectors: dict[int, np.ndarray] = field(default_factory=dict)
    skipped: dict[int, str] = field(default_factory=dict)
    from_cache: int = 0
    requested: int = 0
    requests: int = 0


def spec_for(base_url: str, model: str | None) -> EmbeddingSpec:
    if not model:
        raise OperatorError(
            "endpoint_model_missing",
            "The embeddings endpoint names no model; set it in Settings → Endpoints → embeddings.",
            {"setting": "endpoint_roles.embeddings"},
        )
    return EmbeddingSpec(
        server_origin(base_url), model, int(get_settings().curation_embed_max_input_chars)
    )


def _read_cache(spec: EmbeddingSpec) -> dict[str, list[float]]:
    directory = embeddings_cache_dir(spec.spec_hash)
    if not directory.is_dir():
        return {}
    out: dict[str, list[float]] = {}
    for part in sorted(directory.glob("part-*.parquet")):
        table = pq.read_table(part)
        for key, vector in zip(
            table.column("row_key").to_pylist(), table.column("vector").to_pylist(), strict=True
        ):
            out[str(key)] = vector
    return out


def _write_cache(spec: EmbeddingSpec, rows: dict[str, list[float]]) -> None:
    if not rows:
        return
    directory = embeddings_cache_dir(spec.spec_hash)
    directory.mkdir(parents=True, exist_ok=True)
    table = pa.table(
        {
            "row_key": pa.array(list(rows), pa.string()),
            "vector": pa.array(list(rows.values()), pa.list_(pa.float32())),
        }
    )
    atomic_write(directory / f"part-{uuid.uuid4().hex}.parquet", lambda f: pq.write_table(table, f))


def _request(
    caller: EndpointCaller, spec: EmbeddingSpec, texts: list[str], lease_id: str | None
) -> list[list[float]]:
    try:
        response = caller.call(
            "POST",
            EMBED_PATH,
            body={"model": spec.model, "input": texts},
            purpose="scoring",
            openai=True,
            lease_id=lease_id,
        )
    except EndpointCallError as exc:
        raise OperatorError(
            "embedding_call_failed",
            f"The embeddings endpoint refused or failed: {exc.message}",
            {"code": exc.code},
        ) from None
    data = response.body.get("data") if isinstance(response.body, dict) else None
    if not isinstance(data, list) or len(data) != len(texts):
        raise OperatorError(
            "embedding_response_invalid",
            f"The embeddings endpoint answered {len(data) if isinstance(data, list) else 'no'} "
            f"vectors for {len(texts)} inputs.",
            {"expected": len(texts)},
        )
    ordered = sorted(data, key=lambda d: int(d.get("index", 0)) if isinstance(d, dict) else 0)
    vectors: list[list[float]] = []
    for item in ordered:
        vector = item.get("embedding") if isinstance(item, dict) else None
        if not isinstance(vector, list) or not vector:
            raise OperatorError(
                "embedding_response_invalid", "An embedding in the answer is not a list of numbers."
            )
        vectors.append([float(x) for x in vector])
    return vectors


def embed(
    caller: EndpointCaller,
    spec: EmbeddingSpec,
    keys: Sequence[str],
    texts: Sequence[str],
    *,
    lease_id: str | None = None,
    check_cancel: Any = None,
) -> Embedded:
    """Vectors for ``texts`` (one per row key), from the cache or the endpoint, in batches."""
    out = Embedded(spec)
    cache = _read_cache(spec)
    todo: list[int] = []
    for i, (key, text) in enumerate(zip(keys, texts, strict=True)):
        if not text.strip():
            out.skipped[i] = EMPTY_INPUT
        elif len(text) > spec.max_input_chars:
            out.skipped[i] = OVER_INPUT_CAP
        elif key in cache:
            out.vectors[i] = np.asarray(cache[key], dtype=np.float32)
            out.from_cache += 1
        else:
            todo.append(i)
    batch = int(get_settings().curation_embed_batch_size)
    fresh: dict[str, list[float]] = {}
    dims: int | None = next((len(v) for v in out.vectors.values()), None)
    for start in range(0, len(todo), batch):
        if check_cancel is not None:
            check_cancel()
        chunk = todo[start : start + batch]
        vectors = _request(caller, spec, [texts[i] for i in chunk], lease_id)
        out.requests += 1
        for i, vector in zip(chunk, vectors, strict=True):
            if dims is None:
                dims = len(vector)
            if len(vector) != dims:
                raise OperatorError(
                    "embedding_response_invalid",
                    f"The embeddings endpoint changed dimension mid-run ({dims} then "
                    f"{len(vector)}).",
                )
            out.vectors[i] = np.asarray(vector, dtype=np.float32)
            fresh.setdefault(str(keys[i]), vector)
    out.requested = len(todo)
    _write_cache(spec, fresh)
    return out
