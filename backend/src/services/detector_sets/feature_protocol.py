"""The ``millm_sae_features`` label-run protocol: feature tagging (009 FR-009.65, FR-009.67,
FR-009.68, FR-009.74).

FTDD 009 section 6.3 specified ``feature_tagger@1`` as a 003 operator writing a label run of kind
``feature_tag``. That is the same contradiction the operator resolved for probe verdicts on
2026-10-07 (an operator has no database session and cannot write a label run), so the tagger is a
005 label-run protocol by the same reasoning, recorded in the 009 FTASKS. The FILTER that reads a
tag run stays an operator (``operators/native/detector/feature_filter.py``).

- One ``POST /v1/completions`` per row in scoring mode with ``return_sae_activations``
  (``clients/labelers/sae_activations.py``); nothing is generated.
- The read point must be ``unsteered`` (miLLM FR-27.3a, X-09); anything else stops the run
  ``READ_POINT_MISMATCH``. The SAE miLLM reports must be the one recorded in the identity.
- Each label is outcome ``tagged`` with the SAE, layer, read point, positions and their top
  features as parsed value. A missing SAE, the positions x top_k cap and a GGUF model are refused
  with miLLM's message (FR-009.68).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...clients.endpoint_errors import RowError
from ...clients.labelers.sae_activations import Activations
from ...core.clock import utc_now
from .. import labeling_rules
from ..label_store import LabelRecord
from .probe_protocol import ProbeRefused

PROTOCOL = "millm_sae_features"
ROLE = "features"
OUTCOME = "tagged"
READ_POINT = "unsteered"
NOT_REPORTED = labeling_rules.NOT_REPORTED

REFUSALS: dict[str, tuple[str, str]] = {
    "sae_not_attached": ("SAE_NOT_ATTACHED", "Attach the SAE in miLLM, then resume the run."),
    "sae_activations_refused": (
        "SAE_ACTIVATIONS_REFUSED",
        "Lower top_k or the positions, or name the SAE; miLLM's message says which.",
    ),
    "engine_unsupported": (
        "SAE_GGUF_UNSUPPORTED",
        "miLLM serves this model through llama.cpp (GGUF), which has no layer to read. Load the "
        "transformers build.",
    ),
}


def refusal_for(exc: RowError) -> ProbeRefused | None:
    if exc.server_code not in REFUSALS:
        return None
    code, next_step = REFUSALS[exc.server_code]
    return ProbeRefused(
        code,
        f"miLLM refused the SAE read: {exc.message}. {next_step}",
        {"millm_code": exc.server_code.upper(), "millm_status": exc.status},
    )


def identity(
    *,
    model_id: str,
    model_revision: str | None,
    sae_id: str,
    layer: int | None,
    top_k: int,
    positions: str,
    features: list[int] | None,
) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL,
        "model_id": model_id,
        "model_revision": model_revision if model_revision is not None else NOT_REPORTED,
        "sae_id": sae_id,
        "layer": layer if layer is not None else NOT_REPORTED,
        "top_k": top_k,
        "positions": positions,
        "features": features,
        "read_point": READ_POINT,
        "sent": "one input per request",
    }


def record_for(
    row_key: str, fields: Mapping[str, Any], read: Activations, *, sae_id: str
) -> LabelRecord:
    block = read.block
    if block is None:
        raise ProbeRefused(
            "SAE_ACTIVATIONS_MISSING",
            "miLLM answered without the millm.sae_activations block it was asked for; the run "
            "stopped rather than record a row with no tags.",
        )
    if block["read_point"] != READ_POINT:
        raise ProbeRefused(
            "READ_POINT_MISMATCH",
            f"miLLM read the SAE at {block['read_point']!r}, not {READ_POINT!r}: tagging reads in "
            "scoring mode, where every SAE is suppressed (X-09). The run stopped.",
            {"read_point": block["read_point"]},
        )
    if block["sae_id"] != sae_id:
        raise ProbeRefused(
            "SAE_CHANGED",
            f"miLLM read SAE {block['sae_id']}, not {sae_id} as recorded when the run started.",
        )
    text = fields.get("text")
    return LabelRecord(
        row_key=row_key,
        outcome=OUTCOME,
        parsed_value={
            "sae_id": block["sae_id"],
            "layer": block["layer"],
            "read_point": block["read_point"],
            "positions": block["positions"],
            "prompt_tokens": read.prompt_tokens,
            "chars": len(text) if isinstance(text, str) else None,
        },
        probability=None,
        distribution=None,
        raw_output={"note": block.get("note")},
        rationale=None,
        steering_state=read.steering_header or labeling_rules.UNSTEERED_SCORING,
        latency_ms=read.latency_ms,
        skip_reason=None,
        scored_at=utc_now(),
    )
