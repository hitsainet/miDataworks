"""``openai_chat`` judge (FR-005.41 – FR-005.43; FTDD 005 section 6.3).

Rubric-rendered ``messages``; temperature 0 unless the run says otherwise; the run's seed; and
``response_format: {type: json_schema, ...}`` only when the run's structured-output mode is
``json_schema``. That mode is decided ONCE at run start by :func:`decide_structured_mode`: one
probe request, honoured → ``json_schema``; refused (``response_format_unsupported``, GGUF in v1,
miLLM FR-25.11.1) or not valid under the schema → ``strict_parse``.

A seed counts as confirmed only when echoed (``X-miLLM-Seed``, miLLM FR-25.13.6); otherwise the run
records "seed not confirmed". Pairwise rubrics are asked in BOTH orders; the engine combines the two
with ``labeling_rules.swap_and_agree``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...schemas.labeling import RubricBody
from ..endpoint_caller import SEED_HEADER, STEERING_HEADER, CallResponse, EndpointCaller
from ..endpoint_errors import RowError
from .base import JudgeResult, ProtocolUnsupported, RenderedInput
from .rubric_parsers import PARSERS, ParseFailure


def _content(body: Any) -> str:
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise ProtocolUnsupported("the response has no choices[0].message.content") from None
    return content if isinstance(content, str) else ""


def rubric_values(fields: Mapping[str, Any], question: str | None) -> dict[str, str]:
    """The names a rubric message may use: the row's fields (``None`` as "") and ``question``."""
    values = {k: ("" if v is None else str(v)) for k, v in fields.items()}
    values["question"] = question or ""
    return values


def render_rubric_message(content: str, values: Mapping[str, str]) -> str:
    """One rubric message, rendered exactly as a judge run renders it (a Python format string).
    The rubric library validates every message with this function at create, clone and import,
    and the label-run plan re-checks a stored rubric, so a rubric that cannot render is refused
    before any row is sent."""
    try:
        return content.format_map(values)
    except (KeyError, IndexError, ValueError, AttributeError, TypeError) as exc:
        details: dict[str, object] = {"exception": type(exc).__name__, "reason": str(exc)}
        if isinstance(exc, KeyError) and exc.args:
            details["name"] = str(exc.args[0])
        raise RowError(f"the rubric could not be rendered: {exc}", details=details) from None


class OpenAIChatJudge:
    def __init__(
        self,
        caller: EndpointCaller,
        rubric: RubricBody,
        model: str,
        *,
        sampling: Mapping[str, Any] | None = None,
        structured_output: str = "strict_parse",
        lease_id: str | None = None,
    ) -> None:
        self.caller = caller
        self.rubric = rubric
        self.model = model
        self.sampling = dict(sampling or {})
        self.structured_output = structured_output
        self.lease_id = lease_id

    def _messages(self, fields: Mapping[str, Any], question: str | None) -> list[dict[str, str]]:
        values = rubric_values(fields, question)
        return [
            {"role": m.role, "content": render_rubric_message(m.content, values)}
            for m in self.rubric.messages
        ]

    def request_body(self, fields: Mapping[str, Any], question: str | None) -> dict[str, Any]:
        temperature = self.sampling.get("temperature")
        body: dict[str, Any] = {
            "model": self.model,
            "messages": self._messages(fields, question),
            "temperature": 0.0 if temperature is None else float(temperature),
        }
        if self.sampling.get("seed") is not None:
            body["seed"] = int(self.sampling["seed"])
        if self.sampling.get("max_tokens") is not None:
            body["max_tokens"] = int(self.sampling["max_tokens"])
        if self.structured_output == "json_schema" and self.rubric.json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "verdict", "schema": self.rubric.json_schema},
            }
        return body

    def _ask(
        self, fields: Mapping[str, Any], question: str | None
    ) -> tuple[CallResponse, dict[str, Any]]:
        body = self.request_body(fields, question)
        response = self.caller.call(
            "POST",
            "/v1/chat/completions",
            body=body,
            purpose="judge",
            openai=True,
            lease_id=self.lease_id,
        )
        return response, body

    def _parse(self, text: str) -> tuple[str | None, float | None, str | None]:
        parser = PARSERS[self.rubric.parser]
        try:
            parsed = parser(text, self.rubric.allowed_verdicts, self.rubric.json_schema)
        except ParseFailure:
            return None, None, None
        return parsed.verdict, parsed.score, parsed.rationale

    def judge(self, row: RenderedInput) -> JudgeResult:
        pairwise = self.rubric.style == "pairwise"
        fields = dict(row.fields)
        if pairwise:
            assert self.rubric.pair_fields is not None
            a_field, b_field = self.rubric.pair_fields
            fields["a"], fields["b"] = row.fields.get(a_field), row.fields.get(b_field)
        response, body = self._ask(fields, row.question)
        text = _content(response.body)
        verdict, score, rationale = self._parse(text)
        raw: dict[str, Any] = {"content": text, "request": body}
        swapped: str | None = None
        latency = response.latency_ms
        if pairwise:
            assert self.rubric.swap_map is not None
            swapped_fields = dict(fields)
            swapped_fields["a"], swapped_fields["b"] = fields["b"], fields["a"]
            second, second_body = self._ask(swapped_fields, row.question)
            second_text = _content(second.body)
            second_verdict, _, _ = self._parse(second_text)
            swapped = (
                self.rubric.swap_map.get(second_verdict, second_verdict)
                if second_verdict is not None
                else None
            )
            raw["swapped"] = {"content": second_text, "request": second_body}
            latency += second.latency_ms
        body_json = response.body if isinstance(response.body, dict) else {}
        return JudgeResult(
            verdict=verdict,
            swapped_verdict=swapped,
            score=score,
            rationale=rationale,
            raw_output=raw,
            parse_ok=verdict is not None and (not pairwise or swapped is not None),
            latency_ms=latency,
            seed_echo=response.header(SEED_HEADER),
            steering_header=response.header(STEERING_HEADER),
            response_model=body_json.get("model"),
            system_fingerprint=body_json.get("system_fingerprint"),
            pairwise=pairwise,
        )


def decide_structured_mode(
    caller: EndpointCaller,
    rubric: RubricBody,
    model: str,
    probe_fields: Mapping[str, Any],
    question: str | None,
    *,
    lease_id: str | None = None,
) -> str:
    """One probe request at run start (FR-005.43): ``json_schema`` when honoured, else
    ``strict_parse``. A rubric without a JSON schema is always ``strict_parse``."""
    if rubric.parser != "json_v1" or rubric.json_schema is None:
        return "strict_parse"
    judge = OpenAIChatJudge(
        caller, rubric, model, structured_output="json_schema", lease_id=lease_id
    )
    try:
        result = judge.judge(RenderedInput("probe", dict(probe_fields), question))
    except RowError:
        return "strict_parse"
    return "json_schema" if result.parse_ok else "strict_parse"
