"""miLLM's Batch API as a label-run transport (FR-005.35; miLLM contract v1.11 section 4f).

Built because miLLM serves it (ADR-027 check, 005 FTASKS 1.4: ``POST /v1/files``,
``POST /v1/batches``, ``GET /v1/batches/{id}``, ``GET /v1/files/{id}/content`` and
``POST /v1/batches/{id}/lease`` in ``millm/api/routes/openai/batches.py``, ``files.py``). A batch is
created with the shared lease's ``X-miLLM-Lease`` (miLLM FR-26.7.5), so it runs under the caller's
lease and never takes or releases one of its own. ``pack`` is sent ``false``: bfloat16 scoring is not
batch-invariant (FR-005.34; miLLM measured 0.57% of rows changing their top token when packed).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from .endpoint_caller import EndpointCaller
from .endpoint_errors import EndpointCallError, ProtocolUnsupported

TERMINAL = frozenset({"completed", "failed", "expired", "cancelled"})


class BatchRefused(EndpointCallError):
    code = "BATCH_REFUSED"


def jsonl(lines: Sequence[dict[str, Any]]) -> bytes:
    return "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines).encode("utf-8")


def submit(
    caller: EndpointCaller,
    lines: Sequence[dict[str, Any]],
    *,
    endpoint: str,
    lease_id: str | None,
    metadata: dict[str, str],
) -> dict[str, Any]:
    """Upload the JSONL and create the batch under the lease. Returns the batch object."""
    uploaded = caller.upload(
        "/v1/files", filename="label-run.jsonl", content=jsonl(lines), fields={"purpose": "batch"}
    )
    if uploaded.status != 200 or not isinstance(uploaded.body, dict) or "id" not in uploaded.body:
        raise BatchRefused(f"miLLM refused the batch file ({uploaded.status}).")
    created = caller.raw(
        "POST",
        "/v1/batches",
        body={
            "input_file_id": uploaded.body["id"],
            "endpoint": endpoint,
            "completion_window": "24h",
            "metadata": metadata,
            "pack": False,
        },
        openai=True,
        lease_id=lease_id,
    )
    if created.status != 200 or not isinstance(created.body, dict):
        raise BatchRefused(f"miLLM refused the batch ({created.status}): {created.body}")
    return created.body


def get(caller: EndpointCaller, batch_id: str) -> dict[str, Any]:
    response = caller.raw("GET", f"/v1/batches/{batch_id}", openai=True)
    if response.status != 200 or not isinstance(response.body, dict):
        raise BatchRefused(f"miLLM answered {response.status} for batch {batch_id}.")
    return response.body


def cancel(caller: EndpointCaller, batch_id: str) -> None:
    caller.raw("POST", f"/v1/batches/{batch_id}/cancel", openai=True)


def output_lines(caller: EndpointCaller, file_id: str | None) -> list[dict[str, Any]]:
    if not file_id:
        return []
    status, content = caller.raw_bytes("GET", f"/v1/files/{file_id}/content")
    if status != 200:
        raise BatchRefused(f"miLLM answered {status} for results file {file_id}.")
    out = []
    for line in content.decode("utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                raise ProtocolUnsupported("a batch results line is not JSON") from None
    return out
