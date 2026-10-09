"""The ``millm_probe_score`` label-run protocol (009 FR-009.45 - FR-009.51, FR-009.77, FR-009.81,
FR-009.83; operator decision 2026-10-07).

**Why a label-run protocol, not a recipe operator.** FR-009.47 asks for a probe-verdict step that
writes a 005 label run of kind ``probe_verdict``. 003's operators get no database session, so they
cannot create a label run, and 005's engine had no probe protocol. The operator decided (2026-10-07)
that probe scoring is a label-run protocol beside ``openai_scoring`` and ``tei_classification``. A
probe-verdict run is therefore an ordinary label run: started from the Labeling screen, the REST
API or ``dataworks_start_label_run``, with 005's lease, chunks, resume, cancel and identity. Task
12.4's ``probe_verdict_labeler@1`` operator is NOT built; this module is what 12.4 became.

The endpoint is miLLM at ``MILLM_BASE_URL`` (FTID 009 section 7.7), not one of the four endpoint
roles: the probe's model is whatever miLLM holds, and the probe itself names the model it needs.

This module holds what is specific to probes; 005's engine and plan call it:

- :func:`read_probe` reads ``GET /api/probes/{id}`` (miLLM ``_probe_summary`` plus ``definition``).
  A ``404`` is the "not imported" refusal, with miLLM's message.
- :func:`identity` is the labeler identity (FR-009.47): protocol, the RESIDENT model and its
  revision, the miLLM probe ID, the source miStudio probe ID (``definition.provenance.probe_id``),
  the threshold revision, the window and the input form. A fact miLLM did not report reads
  "not reported" — never a guessed value.
- :func:`record_for` turns ONE scored input into a label (FR-009.48 - FR-009.50, FR-009.81): the
  outcome comes from :func:`verdicts.map_verdict`, and miLLM's verdict is cross-checked against the
  ``>=`` rule (:func:`verdicts.fires`, P-03) on the score and the threshold miLLM reported; a
  disagreement stops the run rather than storing a verdict the bar does not support.
- :func:`refusal_for` names miLLM's refusals of a whole request (FR-009.51): a probe that is not
  imported, an identity or precision mismatch, a GGUF model (no module tree to hook), no model
  loaded, a scope miLLM cannot score, and a request miLLM refuses (a defect here).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ...clients.endpoint_caller import EndpointCaller
from ...clients.endpoint_errors import EndpointCallError, RowError
from ...clients.labelers.probe_score import ProbeScore
from ...core.clock import utc_now
from ...core.config import get_settings
from ...core.errors import AppError
from .. import labeling_rules
from ..label_store import LabelRecord
from . import verdicts

PROTOCOL = "millm_probe_score"
#: The request role a probe-verdict run is started with (``LabelRunStart.role``).
ROLE = "probe"
#: miLLM's window names (``millm/services/probe_scope.py`` ``WINDOWS``).
WINDOWS: tuple[str, ...] = ("all", "prompt", "response", "last_user")
DEFAULT_WINDOW = "all"
#: The field a row is read from: plain text (sent as ONE user turn) or chat ``messages``.
INPUT_FIELDS: tuple[str, ...] = ("text", "messages")
NOT_REPORTED = labeling_rules.NOT_REPORTED
#: miLLM's score route runs unsteered with every SAE suppressed and sends no steering header
#: (``POST /api/probes/score`` docstring; T-73, X-09) — 005's scoring-mode wording.
STEERING_STATE = labeling_rules.UNSTEERED_SCORING


class ProbeRefused(Exception):
    """A refusal of the whole run, with miLLM's reason where miLLM gave one."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def as_app_error(self, status: int = 409) -> AppError:
        return AppError(self.message, code=self.code, status_code=status, details=self.details)


@dataclass(frozen=True)
class ProbeInfo:
    """What miLLM says about one imported probe (``GET /api/probes/{id}``)."""

    probe_id: str
    name: str
    #: The model the probe was fitted on (miLLM ``probes.hf_id``).
    hf_id: str
    layer: int
    scope: str
    #: The probe's global bar and its revision (each verdict reports the bar it was judged at).
    threshold: float | None
    threshold_revision: int
    #: ``{window: bar}`` for windows that placed a bar; absent = provisional (miLLM's rule).
    window_thresholds: dict[str, float]
    rung: int | None
    rung_language: str | None
    armed: bool
    #: ``definition.provenance.probe_id`` / ``run_id``: the miStudio probe this one came from.
    mistudio_probe_id: str | None
    mistudio_run_id: str | None
    load_dtype: str | None
    #: ``{window: [{min_tokens, max_tokens, threshold, ...}]}`` from the definition's
    #: ``decision.windows[w].length_bands``: a verdict's own bar may be one of these, not the
    #: window's bar (2026-10-08 finding 4). Empty when the definition records none.
    length_bands: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def window_bar(self, window: str) -> dict[str, Any]:
        """The bar a window is judged at, as miLLM states it (length bands may refine it per row,
        so each verdict's own ``threshold`` is the authority)."""
        if window in self.window_thresholds:
            return {"threshold": self.window_thresholds[window], "provisional": False}
        return {"threshold": self.threshold, "provisional": True}

    def as_dict(self, window: str) -> dict[str, Any]:
        return {
            "probe_id": self.probe_id,
            "name": self.name,
            "hf_id": self.hf_id,
            "layer": self.layer,
            "scope": self.scope,
            "window": window,
            "window_bar": self.window_bar(window),
            "threshold_revision": self.threshold_revision,
            "rung": self.rung,
            "rung_language": self.rung_language,
            "armed": self.armed,
            "mistudio_probe_id": self.mistudio_probe_id or NOT_REPORTED,
            "mistudio_run_id": self.mistudio_run_id or NOT_REPORTED,
            "load_dtype": self.load_dtype or NOT_REPORTED,
        }


def millm_base_url() -> str:
    """miLLM's address (``MILLM_BASE_URL``). Unset → a refusal naming the setting."""
    base = get_settings().millm_base_url
    if not base:
        raise ProbeRefused(
            "PROBE_ENDPOINT_UNCONFIGURED",
            "Probe scoring runs on miLLM, and MILLM_BASE_URL is not set. Set it in the deployment "
            "configuration (k8s/base/config.yaml), then start the run.",
            {"setting": "MILLM_BASE_URL"},
        )
    return base


def _message(body: Any) -> tuple[str | None, str]:
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        err = body["error"]
        return (str(err.get("code")) if err.get("code") else None), str(err.get("message") or "")
    return None, ""


def read_probe(caller: EndpointCaller, probe_id: str) -> ProbeInfo:
    """``GET /api/probes/{id}``; ``404`` → ``PROBE_NOT_IMPORTED`` with miLLM's reason."""
    try:
        response = caller.raw("GET", f"/api/probes/{probe_id}")
    except EndpointCallError as exc:
        raise ProbeRefused(exc.code, exc.message) from None
    code, message = _message(response.body)
    if response.status == 404:
        raise ProbeRefused(
            "PROBE_NOT_IMPORTED",
            f"miLLM has no probe {probe_id} ({code or 'not found'}: {message or 'no message'}). "
            "Import the probe into miLLM from miStudio's published definition, then start the run.",
            {"probe_id": probe_id, "millm_code": code, "millm_message": message},
        )
    if (
        response.status != 200
        or not isinstance(response.body, dict)
        or not response.body.get("success")
    ):
        raise ProbeRefused(
            "PROBE_READ_FAILED",
            f"miLLM answered {response.status} reading probe {probe_id}: {code or ''} {message}".strip(),
            {"probe_id": probe_id, "status": response.status, "millm_code": code},
        )
    data = response.body["data"]
    definition = data["definition"] or {}
    provenance = definition.get("provenance") or {}
    threshold = data["threshold"]
    return ProbeInfo(
        probe_id=str(data["id"]),
        name=str(data["name"]),
        hf_id=str(data["hf_id"]),
        layer=int(data["layer"]),
        scope=str(data["scope"]),
        threshold=float(threshold) if threshold is not None else None,
        threshold_revision=int(data["threshold_revision"]),
        window_thresholds={str(k): float(v) for k, v in (data["window_thresholds"] or {}).items()},
        rung=data["rung"],
        rung_language=data["rung_language"],
        armed=bool(data["armed"]),
        mistudio_probe_id=provenance.get("probe_id"),
        mistudio_run_id=provenance.get("run_id"),
        load_dtype=data["load_dtype"],
        length_bands=_length_bands(definition),
    )


def _length_bands(definition: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    windows = (definition.get("decision") or {}).get("windows") or {}
    out: dict[str, list[dict[str, Any]]] = {}
    for name, spec in windows.items():
        bands = (spec or {}).get("length_bands") or []
        out[str(name)] = [dict(b) for b in bands if isinstance(b, dict)]
    return out


def _num(value: Any) -> str:
    return "not reported" if value is None else f"{float(value):.2f}"


def bar_source(
    probe: ProbeInfo, window: str, threshold: float | None, n_tokens: int | None
) -> dict[str, Any]:
    """Which bar one verdict was judged at, named (2026-10-08 finding 4).

    miLLM reports only the number. A verdict's ``threshold`` is the window's own bar, or — when the
    probe's definition records length bands for the window — the band holding the input's scored
    token count. The live preflight read "against bar 20.42" beside a window bar of 25.29 with no
    word for which was which. The band is found by its threshold (the number miLLM judged at) and,
    when several bands share it, the token count; nothing matched is said, never assumed."""
    bar = probe.window_bar(window)
    out: dict[str, Any] = {
        "window": window,
        "window_threshold": bar["threshold"],
        "window_provisional": bar["provisional"],
        "threshold": threshold,
        "n_tokens": n_tokens,
    }
    if threshold is None:
        return {
            **out,
            "kind": "none",
            "label": f"no bar; window {window!r} bar: {_num(bar['threshold'])}",
        }
    bands = [b for b in probe.length_bands.get(window, []) if b.get("threshold") == threshold]
    if len(bands) > 1 and n_tokens is not None:
        bands = [
            b
            for b in bands
            if int(b.get("min_tokens") or 0) <= n_tokens
            and (b.get("max_tokens") is None or n_tokens <= int(b["max_tokens"]))
        ]
    if len(bands) == 1:
        band = bands[0]
        lo, hi = band.get("min_tokens"), band.get("max_tokens")
        span = f"{lo if lo is not None else 0}–{hi} tokens" if hi is not None else f"{lo}+ tokens"
        return {
            **out,
            "kind": "length_band",
            "band": {"min_tokens": lo, "max_tokens": hi},
            "label": f"length band {span}: {_num(threshold)}; window {window!r} bar: "
            f"{_num(bar['threshold'])}",
        }
    if bar["threshold"] is not None and float(bar["threshold"]) == float(threshold):
        return {
            **out,
            "kind": "window",
            "label": f"window {window!r} bar: {_num(threshold)}"
            + (" (provisional: the probe's global bar)" if bar["provisional"] else ""),
        }
    return {
        **out,
        "kind": "not_described",
        "label": f"bar {_num(threshold)}, which the probe's definition does not describe; "
        f"window {window!r} bar: {_num(bar['threshold'])}",
    }


def input_form(field_map: Mapping[str, str]) -> str:
    """``one_user_turn`` for a text column (FR-009.77) or ``messages`` for a chat column."""
    keys = sorted(field_map)
    if keys == ["text"]:
        return "one_user_turn"
    if keys == ["messages"]:
        return "messages"
    raise ProbeRefused(
        "FIELD_MAP_INVALID",
        'A probe reads one column: map "text" (sent as one user turn) or "messages" (a chat '
        f"column) to it. Got {keys or 'nothing'}.",
        {"field_map": dict(field_map)},
    )


#: How many rows the plan reads to see whether a text column holds chats as JSON text.
JSON_CHAT_SAMPLE = 20


def check_input_column(column: str, kind: Any, form: str, values: list[Any]) -> None:
    """The mapped column must hold what the input form sends (2026-10-08 live finding 1).

    A probe run sent ``Arrrlex/models-under-pressure``'s chats — JSON TEXT — to miLLM as one user
    turn, because the run mapped ``text`` and nothing looked at the values. So: ``messages`` needs a
    list column, ``text`` needs a string column, and a ``text`` column whose values parse as chats
    is refused, naming ``chat_json_parser``. ``values`` is a sample of the column."""
    import pyarrow as pa

    from ...operators.native.curation.chat_json_parser import Dropped, parse_chat_json

    is_list = pa.types.is_list(kind) or pa.types.is_large_list(kind)
    is_text = pa.types.is_string(kind) or pa.types.is_large_string(kind)
    if form == "messages" and not is_list:
        raise ProbeRefused(
            "FIELD_MAP_INVALID",
            f"{column!r} holds {kind}, not a list of messages, so it cannot be sent as a chat. If it "
            "holds chats as JSON text, add the chat_json_parser step to the recipe, build a "
            'version, and label that; otherwise map it to "text" (one user turn).',
            {"column": column, "type": str(kind), "input_form": form},
        )
    if form == "one_user_turn" and is_list:
        raise ProbeRefused(
            "FIELD_MAP_INVALID",
            f'{column!r} holds chats (a list of messages); map it to "messages" so each row is '
            "sent as the chat it holds, not as text.",
            {"column": column, "type": str(kind), "input_form": form},
        )
    if form == "one_user_turn" and not is_text:
        raise ProbeRefused(
            "FIELD_MAP_INVALID",
            f"{column!r} holds {kind}, not text; a probe reads text or a chat.",
            {"column": column, "type": str(kind), "input_form": form},
        )
    if form == "one_user_turn":
        chats = 0
        for value in values:
            try:
                parse_chat_json(value, column, "drop")
            except Dropped:
                continue
            chats += 1
        if chats:
            raise ProbeRefused(
                "INPUT_IS_JSON_CHAT",
                f"{chats} of {len(values)} sampled values of {column!r} are chats stored as JSON "
                'text. Sent as "text", miLLM would score the brackets and quotes as one user '
                f"turn. Add the chat_json_parser step (columns: [{column!r}]) to the recipe, build "
                'a version, and map the parsed column to "messages".',
                {"column": column, "sampled": len(values), "json_chats": chats},
            )


def identity(
    probe: ProbeInfo,
    *,
    model_id: str,
    model_revision: str | None,
    window: str,
    form: str,
) -> dict[str, Any]:
    """The labeler identity of a probe-verdict run (FR-009.47). Nothing is filled in."""
    return {
        "protocol": PROTOCOL,
        "model_id": model_id,
        "model_revision": model_revision if model_revision is not None else NOT_REPORTED,
        "probe_id": probe.probe_id,
        "mistudio_probe_id": probe.mistudio_probe_id or NOT_REPORTED,
        "threshold_revision": probe.threshold_revision,
        "window": window,
        "input_form": form,
        "sent": "one input per request",
    }


# --- refusals of a whole request (FR-009.51) --------------------------------------------------

#: miLLM's error code (lower-cased by 005's caller) -> (our code, what to do next).
REFUSALS: dict[str, tuple[str, str]] = {
    "probe_not_found": (
        "PROBE_NOT_IMPORTED",
        "Import the probe into miLLM from miStudio's published definition, then resume the run.",
    ),
    "probe_model_mismatch": (
        "PROBE_IDENTITY_MISMATCH",
        "Load the model the probe was fitted on in miLLM, or score with a probe fitted on the "
        "loaded model.",
    ),
    "probe_dtype_mismatch": (
        "PROBE_IDENTITY_MISMATCH",
        "Load the model at the precision and quantization the probe was fitted under.",
    ),
    "probe_hook_unsupported": (
        "PROBE_GGUF_UNSUPPORTED",
        "miLLM serves this model through llama.cpp (GGUF), which has no layers to read. Load the "
        "transformers build of the model in miLLM.",
    ),
    "probe_no_model_loaded": (
        "MODEL_NOT_LOADED",
        "Load the probe's model in miLLM, then resume the run.",
    ),
    "probe_scope_unverifiable": (
        "PROBE_SCOPE_UNSCORABLE",
        "Re-export the probe from miStudio with scope 'all'.",
    ),
    "invalid_probe_score_request": (
        "PROBE_REQUEST_REFUSED",
        "miLLM refused the request miDataworks built; this is a defect in miDataworks.",
    ),
}


def refusal_for(exc: RowError) -> ProbeRefused | None:
    """miLLM's refusal of the whole request, named; None for a row-level failure."""
    if exc.server_code not in REFUSALS:
        return None
    code, next_step = REFUSALS[exc.server_code]
    reason = exc.message
    return ProbeRefused(
        code,
        f"miLLM refused the probe score: {reason}. {next_step}",
        {
            "millm_code": exc.server_code.upper(),
            "millm_status": exc.status,
            "millm_details": {k: v for k, v in exc.details.items() if k not in ("user_message",)},
        },
    )


# --- one scored input -> one label (FR-009.48 - FR-009.50, FR-009.81) -------------------------


def _reported(source: Mapping[str, Any], key: str) -> Any:
    """miLLM's value as sent; a key miLLM did not send reads "not reported"."""
    return source[key] if key in source else NOT_REPORTED


def _chars(fields: Mapping[str, Any]) -> int:
    value = fields.get("text")
    if isinstance(value, str):
        return len(value)
    messages = fields.get("messages") or []
    return sum(len(str(m.get("content", ""))) for m in messages if isinstance(m, Mapping))


def the_verdict(scored: ProbeScore, probe_id: str, window: str) -> dict[str, Any]:
    """The one verdict for (probe, window). Anything else is a contract break: stop the run."""
    matching = [v for v in scored.verdicts if v["probe_id"] == probe_id and v["window"] == window]
    if len(matching) != 1:
        raise ProbeRefused(
            "PROBE_RESPONSE_UNEXPECTED",
            f"miLLM returned {len(matching)} verdicts for probe {probe_id} window {window} "
            f"(of {len(scored.verdicts)} in all); exactly one was asked for.",
            {"verdicts": scored.verdicts},
        )
    return matching[0]


def check_boundary(verdict: Mapping[str, Any]) -> None:
    """miLLM's verdict must agree with ``score >= threshold`` (P-03) on the figures it reported.

    A null verdict (the probe said nothing) and a provisional one carry no claim to check. A
    disagreement means miLLM decided with another rule; the run stops rather than storing it.
    """
    value = verdict["verdict"]
    score, threshold = verdict["score"], verdict["threshold"]
    if value is None or score is None or threshold is None:
        return
    expected = verdicts.fires(float(score), float(threshold))
    if bool(value) != expected:
        raise ProbeRefused(
            "VERDICT_BOUNDARY_DISAGREES",
            f"miLLM reported verdict {value} for score {score} against threshold {threshold}; "
            f"the shared rule (score >= threshold fires, P-03) gives {expected}. The run stopped "
            "rather than store a verdict the bar does not support.",
            {"verdict": dict(verdict)},
        )


def record_for(
    row_key: str,
    fields: Mapping[str, Any],
    scored: ProbeScore,
    *,
    probe_id: str,
    window: str,
    threshold_revision: int,
) -> LabelRecord:
    """The label for one scored row."""
    now = utc_now()
    if scored.error is not None:
        # TOKENIZATION_FAILED and other per-input errors: this row cannot be scored by miLLM.
        return LabelRecord(
            row_key=row_key,
            outcome="skipped",
            parsed_value={"chars": _chars(fields), "error": scored.error},
            probability=None,
            distribution=None,
            raw_output={"result": scored.result},
            rationale=None,
            steering_state=STEERING_STATE,
            latency_ms=scored.latency_ms,
            skip_reason=str(scored.error.get("code") or "not scored"),
            scored_at=now,
        )
    verdict = the_verdict(scored, probe_id, window)
    if verdict["threshold_revision"] != threshold_revision:
        raise ProbeRefused(
            "THRESHOLD_CHANGED",
            f"Probe {probe_id}'s bar moved to revision {verdict['threshold_revision']} during the "
            f"run, which started at revision {threshold_revision}. Labels so far are kept; start "
            "a new run to score under the new bar.",
            {"started_at": threshold_revision, "now": verdict["threshold_revision"]},
        )
    check_boundary(verdict)
    mapped = verdicts.map_verdict(verdict)
    parsed = {
        "score": verdict["score"],
        "threshold": verdict["threshold"],
        "verdict": verdict["verdict"],
        "window": verdict["window"],
        "rung": _reported(verdict, "rung"),
        "rung_language": _reported(verdict, "rung_language"),
        "provisional": verdict["provisional"],
        "threshold_revision": verdict["threshold_revision"],
        "n_scored_tokens": _reported(verdict, "n_scored_tokens"),
        "not_scored_reason": _reported(verdict, "not_scored_reason"),
        "token_ids": scored.token_ids,
        "n_tokens": scored.n_tokens,
        "prompt_tokens": _reported(scored.result, "prompt_tokens"),
        "input_kind": _reported(scored.result, "input_kind"),
        "chars": _chars(fields),
        "sent_singly": True,
    }
    return LabelRecord(
        row_key=row_key,
        outcome=mapped.outcome,
        parsed_value=parsed,
        probability=None,  # a probe score is not a probability (dw_labels.probability is [0, 1])
        distribution=None,
        raw_output={"verdict": dict(verdict), "model": scored.model},
        rationale=None,
        steering_state=STEERING_STATE,
        latency_ms=scored.latency_ms,
        skip_reason=mapped.reason if mapped.outcome == "skipped" else None,
        scored_at=now,
        provisional=mapped.provisional,
    )


def reported_model(scored: ProbeScore) -> dict[str, Any]:
    """miLLM's ``model`` block (``{hf_id, revision, dtype, quantization}``), "not reported" for a
    key it did not send."""
    return {
        key: (scored.model[key] if scored.model.get(key) is not None else NOT_REPORTED)
        for key in ("hf_id", "revision", "dtype", "quantization")
    }
