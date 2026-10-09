"""Hugging Face Hub API and Dataset Viewer client (FR-001.2, 001.14–001.17, 001.26; 001 FTID §3.1).

Every call goes through :meth:`HubClient._get`, which sends ``Authorization`` only when a token
exists, applies ``HF_HTTP_TIMEOUT_S``, requires a JSON content type (a 200 HTML page is an error,
never an empty success) and maps failures through :func:`map_hf_error`:

| HF answer                          | Code                |
|------------------------------------|---------------------|
| 404, or 401 with no token sent     | ``hf_not_found``    |
| 401 with a token sent              | ``hf_token_rejected`` (details: tier) |
| 403, or a body naming the gate     | ``hf_gated``        |
| timeout, 5xx, non-JSON 200         | ``hf_unavailable``  |

Dataset Viewer failures raise :class:`ViewerUnavailable`, which preview reports as an
``unavailable`` entry. The browser never calls Hugging Face: every call is here, in the backend.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from ..core.config import get_settings
from ..core.errors import AppError


class HfError(AppError):
    """A refusal mapped from a Hugging Face answer; ``code`` is one of the four in the table."""


class ViewerUnavailable(Exception):
    def __init__(self, part: str, reason: str) -> None:
        super().__init__(f"{part}: {reason}")
        self.part = part
        self.reason = reason


MESSAGES = {
    "hf_not_found": "Hugging Face has no dataset {repo} that this token can see. Check the "
    "repository ID; a private dataset needs a token with access.",
    "hf_token_rejected": "Hugging Face rejected the {tier} access token. Replace it in Settings, or "
    "enter a working token for this import.",
    "hf_gated": "This dataset is gated. Accept its terms on the Hub with the account that owns the "
    "token, then import again.",
    "hf_unavailable": "Hugging Face did not answer usefully ({why}). Try again in a minute.",
}


def map_hf_error(
    status: int | None,
    body: str,
    *,
    token_sent: bool,
    repo: str,
    tier: str = "stored",
    why: str = "",
) -> HfError:
    lowered = body.lower()
    if status == 403 or (status in (401, 403) and "gated" in lowered):
        code, http = "hf_gated", 403
    elif status == 404 or (status == 401 and not token_sent):
        code, http = "hf_not_found", 404
    elif status == 401:
        code, http = "hf_token_rejected", 401
    else:
        code, http = "hf_unavailable", 502
    message = MESSAGES[code].format(repo=repo, tier=tier, why=why or f"status {status}")
    details: dict[str, Any] = {"status": status}
    if code == "hf_token_rejected":
        details["tier"] = tier
    return HfError(message, code=code, status_code=http, details=details)


@dataclass(frozen=True)
class ConfigSplit:
    config: str
    split: str


class HubClient:
    def __init__(
        self, token: str | None, *, tier: str = "none", http: httpx.Client | None = None
    ) -> None:
        settings = get_settings()
        self._token = token
        self._tier = tier
        self._hub = settings.hf_hub_url.rstrip("/")
        self._viewer = settings.hf_datasets_server_url.rstrip("/")
        self._http = http or httpx.Client(timeout=settings.hf_http_timeout_s)

    def _get(self, url: str, *, repo: str, params: dict[str, str] | None = None) -> Any:
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        try:
            response = self._http.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise map_hf_error(
                None,
                "",
                token_sent=bool(self._token),
                repo=repo,
                tier=self._tier,
                why=type(exc).__name__,
            ) from None
        if response.status_code != 200:
            raise map_hf_error(
                response.status_code,
                response.text[:500],
                token_sent=bool(self._token),
                repo=repo,
                tier=self._tier,
            )
        if "json" not in response.headers.get("content-type", ""):
            raise map_hf_error(
                200,
                "",
                token_sent=bool(self._token),
                repo=repo,
                tier=self._tier,
                why="the answer was not JSON",
            )
        try:
            return response.json()
        except ValueError:
            raise map_hf_error(
                200,
                "",
                token_sent=bool(self._token),
                repo=repo,
                tier=self._tier,
                why="the answer was not JSON",
            ) from None

    # --- Hub API -----------------------------------------------------------------------------

    def dataset(self, repo_id: str) -> dict[str, Any]:
        """``GET /api/datasets/{repo}``: the default branch's head, whatever it is named."""
        data: dict[str, Any] = self._get(f"{self._hub}/api/datasets/{repo_id}", repo=repo_id)
        return data

    def revision(self, repo_id: str, ref: str) -> dict[str, Any]:
        """``GET /api/datasets/{repo}/revision/{ref}``; resolves branches, tags and short SHAs."""
        data: dict[str, Any] = self._get(
            f"{self._hub}/api/datasets/{repo_id}/revision/{quote(ref, safe='')}", repo=repo_id
        )
        return data

    def file_sizes(self, repo_id: str, commit: str) -> dict[str, int]:
        """File sizes at a commit (``?blobs=true``): the size fallback when the Viewer has none."""
        data = self._get(
            f"{self._hub}/api/datasets/{repo_id}/revision/{commit}",
            repo=repo_id,
            params={"blobs": "true"},
        )
        return {
            str(s.get("rfilename")): int(s["size"])
            for s in data.get("siblings") or []
            if isinstance(s.get("size"), int)
        }

    # --- Dataset Viewer ----------------------------------------------------------------------

    def _viewer_get(self, part: str, path: str, repo_id: str, **params: str) -> Any:
        try:
            return self._get(
                f"{self._viewer}/{path}", repo=repo_id, params={"dataset": repo_id, **params}
            )
        except HfError as exc:
            raise ViewerUnavailable(part, exc.message) from None

    def viewer_splits(self, repo_id: str) -> list[ConfigSplit]:
        data = self._viewer_get("splits", "splits", repo_id)
        return [ConfigSplit(s["config"], s["split"]) for s in data.get("splits", [])]

    def viewer_size(self, repo_id: str, config: str | None) -> dict[str, Any]:
        params = {"config": config} if config else {}
        data: dict[str, Any] = self._viewer_get("size", "size", repo_id, **params)
        return data

    def viewer_first_rows(self, repo_id: str, config: str, split: str) -> dict[str, Any]:
        data: dict[str, Any] = self._viewer_get(
            "first_rows", "first-rows", repo_id, config=config, split=split
        )
        return data
