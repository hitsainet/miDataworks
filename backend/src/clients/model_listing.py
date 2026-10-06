"""Fetch models for an endpoint role (ADR-011; Foundation task 8.3).

Two shapes are understood:
1. OpenAI-compatible ``GET {base}/v1/models`` → ``{"data": [{"id": ...}, ...]}``;
2. Text Embeddings Inference (TEI) ``GET {root}/info`` → ``{"model_id": ...}``, read when the
   first is absent or not OpenAI-shaped (the classifier role's DeBERTa server; C2, P-24).

Each failure has its own code, because miStudio's client once read a 200 HTML page from a
misrouted ingress as an empty SUCCESS:
- ``ENDPOINT_UNREACHABLE``: connection refused, DNS failure or timeout;
- ``ENDPOINT_UNAUTHORIZED``: 401 or 403;
- ``ENDPOINT_NOT_JSON``: a 2xx body that is not JSON (an HTML page, for example);
- ``ENDPOINT_UNRECOGNISED``: JSON in neither shape, or no route for either;
- ``ENDPOINT_ERROR``: any other non-2xx status.

Every request sends ``X-miLLM-Strict: true`` (ADR-012); servers other than miLLM ignore it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import httpx

from ..core.errors import AppError

STRICT_HEADER = {"X-miLLM-Strict": "true"}


@dataclass(frozen=True)
class ModelListing:
    models: list[str]
    source: Literal["openai", "tei"]
    url: str


class EndpointError(AppError):
    status_code = 502


def _urls(base_url: str) -> tuple[str, str]:
    base = base_url.rstrip("/")
    root = base[: -len("/v1")] if base.endswith("/v1") else base
    return f"{root}/v1/models", f"{root}/info"


async def _get(client: httpx.AsyncClient, url: str, headers: dict[str, str]) -> httpx.Response:
    try:
        return await client.get(url, headers=headers)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.PoolTimeout) as exc:
        raise EndpointError(
            f"Could not reach {url}. Check the address and that the server is running.",
            code="ENDPOINT_UNREACHABLE",
            details={"url": url, "reason": type(exc).__name__},
        ) from None
    except httpx.HTTPError as exc:
        raise EndpointError(
            f"Could not reach {url}.",
            code="ENDPOINT_UNREACHABLE",
            details={"url": url, "reason": type(exc).__name__},
        ) from None


def _check_auth(response: httpx.Response, url: str) -> None:
    if response.status_code in (401, 403):
        raise EndpointError(
            f"{url} refused the API key ({response.status_code}). Check the key for this role.",
            code="ENDPOINT_UNAUTHORIZED",
            details={"url": url, "status": response.status_code},
        )


def _json(response: httpx.Response, url: str) -> Any:
    try:
        return response.json()
    except ValueError:
        content_type = response.headers.get("content-type", "unknown")
        raise EndpointError(
            f"{url} answered with {content_type}, not JSON. The address may point at a web page "
            "rather than the model server.",
            code="ENDPOINT_NOT_JSON",
            details={"url": url, "content_type": content_type},
        ) from None


def _openai_ids(body: Any) -> list[str] | None:
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        return None
    ids = [item.get("id") for item in body["data"] if isinstance(item, dict)]
    if not ids or not all(isinstance(i, str) and i for i in ids):
        return None
    return [str(i) for i in ids]


async def fetch_models(
    base_url: str,
    api_key: str | None,
    *,
    timeout_s: float = 5.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ModelListing:
    models_url, info_url = _urls(base_url)
    headers = dict(STRICT_HEADER)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with httpx.AsyncClient(timeout=timeout_s, transport=transport) as client:
        response = await _get(client, models_url, headers)
        _check_auth(response, models_url)
        if response.is_success:
            ids = _openai_ids(_json(response, models_url))
            if ids is not None:
                return ModelListing(ids, "openai", models_url)
        elif response.status_code not in (404, 405):
            raise EndpointError(
                f"{models_url} answered {response.status_code}.",
                code="ENDPOINT_ERROR",
                details={"url": models_url, "status": response.status_code},
            )

        info = await _get(client, info_url, headers)
        _check_auth(info, info_url)
        if info.is_success:
            body = _json(info, info_url)
            model_id = body.get("model_id") if isinstance(body, dict) else None
            if isinstance(model_id, str) and model_id:
                return ModelListing([model_id], "tei", info_url)
        raise EndpointError(
            f"{base_url} serves neither an OpenAI-compatible /v1/models nor a TEI /info.",
            code="ENDPOINT_UNRECOGNISED",
            details={"models_url": models_url, "info_url": info_url},
        )
