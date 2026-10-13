"""Build a classifier or judge client for a resolved endpoint (FR-005.13; FTID 005 section 2.1).

:func:`check_binding` is the template–model binding check: a template that names a model (always,
when it names token IDs) refuses an endpoint serving another model, naming both. Token IDs are
tokenizer-specific; two models of one vendor and family were measured sharing 0.00% of token IDs
(miStudio SAE data-path arc, 2026-09-11).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import TypeAdapter

from ...schemas.labeling import (
    OpenAIScoringTemplate,
    PluginTemplate,
    RubricBody,
    TEIClassificationTemplate,
    TemplateBody,
)
from ..endpoint_caller import EndpointCaller
from .base import ClassifierClient, JudgeClient
from .openai_chat_judge import OpenAIChatJudge
from .openai_scoring import OpenAIScoringClient
from .plugins import load_classifier
from .tei import TEIClassifierClient

_TEMPLATE: TypeAdapter[TemplateBody] = TypeAdapter(TemplateBody)


class TemplateModelMismatch(Exception):
    code = "TEMPLATE_MODEL_MISMATCH"

    def __init__(self, bound: str, served: str | None) -> None:
        super().__init__(
            f"This template is bound to {bound}, but the endpoint serves {served or 'no model'}. "
            "Its token IDs would mean different tokens on another model. Use a template bound to "
            f"{served or 'the served model'}, or point the role at {bound}."
        )
        self.bound = bound
        self.served = served


class ProtocolMismatch(Exception):
    code = "PROTOCOL_MISMATCH"


def parse_template(body: Mapping[str, Any]) -> TemplateBody:
    return _TEMPLATE.validate_python(dict(body))


def check_binding(template: TemplateBody, model: str | None) -> None:
    """Refuse an endpoint model that differs from the template's bound model."""
    bound = template.bound_model_id
    if bound is not None and bound != model:
        raise TemplateModelMismatch(bound, model)


def build_classifier(
    caller: EndpointCaller,
    protocol: str,
    template: TemplateBody,
    model: str,
    *,
    lease_id: str | None = None,
) -> ClassifierClient:
    check_binding(template, model)
    if protocol != template.kind:
        raise ProtocolMismatch(
            f"The classifier role speaks {protocol}, but the template is for {template.kind}."
        )
    if isinstance(template, OpenAIScoringTemplate):
        return OpenAIScoringClient(caller, template, model, lease_id=lease_id)
    if isinstance(template, TEIClassificationTemplate):
        return TEIClassifierClient(caller, template, model)
    assert isinstance(template, PluginTemplate)
    return load_classifier(template.entry_point, dict(template.options))


def build_judge(
    caller: EndpointCaller,
    rubric: RubricBody,
    model: str,
    *,
    sampling: Mapping[str, Any] | None = None,
    structured_output: str = "strict_parse",
    lease_id: str | None = None,
) -> JudgeClient:
    return OpenAIChatJudge(
        caller,
        rubric,
        model,
        sampling=sampling,
        structured_output=structured_output,
        lease_id=lease_id,
    )
