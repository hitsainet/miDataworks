"""Typed outcomes of a call to a classifier, judge or miLLM endpoint (FTDD 005 section 6.2).

Raised by ``clients/endpoint_caller.py`` — the one place that maps a status code — and handled by
the label-run engine, the sample route and the server probe. Each names what happened; none
carries a key or a lease ID.
"""

from __future__ import annotations


class EndpointCallError(Exception):
    """Base of every typed endpoint outcome. ``code`` is the stable code the API reports."""

    code = "ENDPOINT_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class EndpointUnreachable(EndpointCallError):
    code = "ENDPOINT_UNREACHABLE"


class EndpointUnauthorized(EndpointCallError):
    code = "ENDPOINT_UNAUTHORIZED"


class EndpointNotJson(EndpointCallError):
    code = "ENDPOINT_NOT_JSON"


class ProtocolUnsupported(EndpointCallError):
    """The server answered, but not in the protocol's shape (no logprobs, no ``/predict``)."""

    code = "PROTOCOL_UNSUPPORTED"


class Backpressure(EndpointCallError):
    """``503``: wait ``retry_after`` (or the backoff step) and retry. Never a row failure."""

    code = "BACKPRESSURE"

    def __init__(self, message: str, retry_after: float | None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ContextOverflow(EndpointCallError):
    """The row does not fit the model's context window: skip it, never retry (FR-005.47)."""

    code = "CONTEXT_OVERFLOW"


class ModelNotResident(EndpointCallError):
    """``409 model_not_resident``: the model the run needs is not loaded (FR-005.38/39)."""

    code = "MODEL_NOT_LOADED"

    def __init__(self, message: str, requested: str | None, resident: str | None) -> None:
        super().__init__(message)
        self.requested = requested
        self.resident = resident


class LeaseLost(EndpointCallError):
    """``409 model_leased``, a refused renewal, or a response naming another model."""

    code = "LEASE_LOST"

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class StrictRefusal(EndpointCallError):
    """``400 unused_fields_refused``: a field miDataworks sends is not honoured. A DEFECT in
    miDataworks; the run fails and is never retried."""

    code = "STRICT_REFUSAL"


class RowError(EndpointCallError):
    """Any other ``4xx``, or an answer that cannot become a probability. Counts toward the
    consecutive-failure limit (FR-005.48)."""

    code = "ROW_ERROR"

    def __init__(
        self,
        message: str,
        status: int | None = None,
        *,
        server_code: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        #: The server's own error code, lower-cased as ``_error_fields`` reads it (miLLM's
        #: ``PROBE_MODEL_MISMATCH`` arrives as ``probe_model_mismatch``), or None when it sent none.
        self.server_code = server_code
        #: The server's ``error.details`` object, verbatim (miLLM names every mismatched field).
        self.details = details or {}


class TransientError(EndpointCallError):
    """A ``5xx`` other than 503, or a connection error, after the retries ran out."""

    code = "TRANSIENT_ERROR"
