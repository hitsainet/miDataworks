"""Agent origin, "who", and the approval gate (ADR-013, ADR-014; Foundation tasks 8.7, 9.2, 9.4).

**Origin.** A request carrying ``X-Dataworks-Agent: agent:<name>`` is agent-originated; anything
else is the operator's. A malformed header is refused (``400 INVALID_AGENT_IDENTITY``); an empty
header is malformed, not absent (010 FTDD section 5.1).

**Who** (C5). Operator actions record the ``operator_name`` setting; an empty value refuses the
action with ``422 NO_IDENTITY`` and a pointer to Settings. Agent actions record the agent
identity from the header. A ``who`` field in a request body is never trusted.

**The gate** (C6). :func:`requires_approval_when_agent` wraps a route. Operator calls run
unchanged. Agent calls are stored as a pending approval and answered ``202`` unless the optional
``when`` predicate returns false; a predicate that raises fails CLOSED (gated). The gate lives in
the REST layer, so the MCP server, miStudio's proxy and any future caller all pass through it.
"""

from __future__ import annotations

import functools
import inspect
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, get_type_hints

from fastapi import Depends, Request
from fastapi.params import Depends as DependsParam
from fastapi.responses import JSONResponse
from pydantic import BaseModel, TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .errors import AppError

logger = logging.getLogger(__name__)

AGENT_HEADER = "X-Dataworks-Agent"
_AGENT_VALUE = re.compile(r"^agent:[a-z0-9][a-z0-9-]{0,47}$")

#: The approval action names (010 FTDD section 5.3; operator decisions D6b, P-11, S3-01, S3-08).
#: Features attach these to their routes; Foundation attaches `secret_write` to the settings
#: and endpoint-key writes.
APPROVAL_ACTIONS: dict[str, str] = {
    "hub_push": "Any push to the Hugging Face Hub, including a detector-set send",
    "agent_label_rows": "Agent label rows over 5,000 per version per 24 h (P-07)",
    "version_delete": "Deleting a version",
    "millm_model_load": "Anything that loads or swaps a model in miLLM (no route in v1, T-37)",
    "secret_write": "Writing an API key or the Hugging Face token (P-11)",
    "source_annotate": "Annotating a source's licence or terms (S3-01)",
    "gate_target_write": "Changing a calibration gate target (S3-08)",
}


@dataclass(frozen=True)
class Actor:
    """Who is calling, as far as the request says."""

    origin: Literal["operator", "agent"]
    agent: str | None = None
    #: Set while an approved action executes, so the action can record its approval.
    approval_id: str | None = None


def get_actor(request: Request) -> Actor:
    """FastAPI dependency: parse the agent header."""
    raw = request.headers.get(AGENT_HEADER)
    if raw is None:
        return Actor(origin="operator")
    if not _AGENT_VALUE.fullmatch(raw):
        raise AppError(
            f"{AGENT_HEADER} must look like agent:<name> (lower case, digits and hyphens).",
            code="INVALID_AGENT_IDENTITY",
            status_code=400,
        )
    return Actor(origin="agent", agent=raw)


@dataclass(frozen=True)
class Who:
    who: str
    origin: Literal["operator", "agent"]


async def resolve_who(actor: Actor, db: AsyncSession) -> Who:
    """The "who" to record for this action, or ``422 NO_IDENTITY``."""
    if actor.origin == "agent":
        assert actor.agent is not None
        return Who(actor.agent, "agent")
    from ..services.app_setting_service import AppSettingService

    name = (await AppSettingService.get_plain(db, "operator_name") or "").strip()
    if not name:
        raise AppError(
            "Set your name in Settings before starting work; it is recorded as who did it.",
            code="NO_IDENTITY",
            status_code=422,
            details={"setting": "operator_name"},
        )
    return Who(name, "operator")


# --------------------------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------------------------

WhenPredicate = Callable[[dict[str, Any], AsyncSession], "bool | Awaitable[bool]"]


@dataclass(frozen=True)
class Executor:
    """How to run a stored request once the operator approves it."""

    key: str
    action: str
    fn: Callable[..., Awaitable[Any]]
    db_param: str
    actor_param: str
    value_params: dict[str, Any]  # name -> resolved annotation
    secret_fields: tuple[str, ...]


#: Every gated route, keyed by ``module.qualname``. Read by the approval service on approve.
EXECUTORS: dict[str, Executor] = {}


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return TypeAdapter(type(value)).dump_python(value, mode="json")


def _split_path(path: str) -> list[str]:
    return [p for p in path.split(".") if p]


def pop_secret(payload: dict[str, Any], path: str) -> Any:
    """Remove the value at a dotted path, returning it (None when absent)."""
    *parents, leaf = _split_path(path)
    node: Any = payload
    for part in parents:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    if isinstance(node, dict) and node.get(leaf) is not None:
        return node.pop(leaf)
    return None


def put_value(payload: dict[str, Any], path: str, value: Any) -> None:
    *parents, leaf = _split_path(path)
    node = payload
    for part in parents:
        node = node.setdefault(part, {})
    node[leaf] = value


def _find_dependency(sig: inspect.Signature, dependency: Callable[..., Any]) -> str | None:
    for name, param in sig.parameters.items():
        if isinstance(param.default, DependsParam) and param.default.dependency is dependency:
            return name
    return None


def requires_approval_when_agent(
    action: str,
    *,
    when: WhenPredicate | None = None,
    secret_fields: tuple[str, ...] = (),
    summary: Callable[[dict[str, Any]], str] | None = None,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Gate a route for agent-originated calls (task 9.2).

    The route must declare ``db: AsyncSession = Depends(get_db)`` and
    ``actor: Actor = Depends(get_actor)``; every other parameter must be a plain value (path,
    query or body) so the stored request can be replayed exactly. Anything else is refused at
    import time, not discovered at approve time.

    ``secret_fields`` are dotted paths into the stored request (``"body.api_key"``). Their values
    are removed from the stored payload, replaced in it by an HMAC, and kept only encrypted.
    """
    if action not in APPROVAL_ACTIONS:
        raise ValueError(f"{action!r} is not a registered approval action")

    def decorate(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        sig = inspect.signature(fn)
        db_param = _find_dependency(sig, get_db)
        actor_param = _find_dependency(sig, get_actor)
        if db_param is None or actor_param is None:
            raise TypeError(
                f"{fn.__qualname__}: a gated route must declare Depends(get_db) and "
                "Depends(get_actor)"
            )
        hints = get_type_hints(fn)
        value_params: dict[str, Any] = {}
        for name, param in sig.parameters.items():
            if name in (db_param, actor_param):
                continue
            if isinstance(param.default, DependsParam):
                raise TypeError(
                    f"{fn.__qualname__}: parameter {name!r} is a dependency the approval "
                    "executor cannot replay"
                )
            value_params[name] = hints.get(name, Any)
        key = f"{fn.__module__}.{fn.__qualname__}"
        EXECUTORS[key] = Executor(
            key, action, fn, db_param, actor_param, value_params, tuple(secret_fields)
        )

        @functools.wraps(fn)
        async def wrapper(**kwargs: Any) -> Any:
            actor: Actor = kwargs[actor_param]
            db: AsyncSession = kwargs[db_param]
            if actor.origin != "agent":
                return await fn(**kwargs)
            values = {name: kwargs[name] for name in value_params}
            gated = True
            if when is not None:
                try:
                    outcome = when(values, db)
                    if inspect.isawaitable(outcome):
                        outcome = await outcome
                    gated = bool(outcome)
                except Exception:
                    # FAIL CLOSED: a predicate that cannot decide gates the call.
                    logger.exception("Approval predicate for %s raised; gating the call", key)
                    gated = True
            if not gated:
                return await fn(**kwargs)

            from ..services.approval_service import ApprovalService

            payload = {name: _to_jsonable(value) for name, value in values.items()}
            approval = await ApprovalService.create_pending(
                db,
                action=action,
                executor_key=key,
                payload=payload,
                secret_fields=secret_fields,
                requested_by=actor.agent or "agent:unknown",
                summary=summary(payload) if summary else EXECUTORS[key].action,
            )
            return JSONResponse(
                status_code=202,
                content={
                    "approval_id": approval.id,
                    "status": approval.status,
                    "action": approval.action,
                    "request_digest": "sha256:" + approval.request_digest,
                    "expires_at": approval.expires_at.isoformat(),
                    "hint": "Waiting for the operator to approve this in miDataworks.",
                },
            )

        # Resolved annotations: FastAPI would otherwise evaluate string annotations against THIS
        # module's globals (the wrapper's), where the route's own types do not exist.
        wrapper.__signature__ = sig.replace(  # type: ignore[attr-defined]
            parameters=[
                p.replace(annotation=hints.get(name, p.annotation))
                for name, p in sig.parameters.items()
            ],
            return_annotation=inspect.Signature.empty,
        )
        wrapper.__dw_approval_action__ = action  # type: ignore[attr-defined]
        wrapper.__dw_executor_key__ = key  # type: ignore[attr-defined]
        return wrapper

    return decorate


ActorDep = Depends(get_actor)
