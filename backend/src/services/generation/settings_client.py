"""Read-only access to miLLM's steering profiles and SAE attachments (FR-007.15, FR-007.18).

miLLM routes (``millm/api/routes/management/profiles.py``, ``saes.py``; envelope
``{success, data, error}``, ``millm/api/schemas/common.py``):

- ``GET /api/profiles`` → ``{profiles: [ProfileResponse], total, active_profile_id}``;
- ``GET /api/profiles/{id}`` → ``ProfileResponse`` (``id, name, model_id, sae_id, layer,
  steering {feature_idx: value at λ=1}, is_active, source_kind, intensity, updated_at``);
- ``GET /api/saes/attachments`` → ``{is_attached, count, entries: [{sae_id, layer, ...}]}``.

THIS MODULE NEVER WRITES TO miLLM: no profile create, patch, activate, deactivate or delete; no
SAE attach; no model load (FR-007.18, FR-007.30). ``test_no_millm_state_writes.py`` walks every
007 module's AST for those paths and for any non-GET method sent to miLLM management routes.
Profiles are read fresh at plan, at start and before every chunk; never cached across chunks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...clients.endpoint_caller import EndpointCaller
from ...clients.endpoint_errors import EndpointCallError, ProtocolUnsupported

PROFILES = "/api/profiles"
ATTACHMENTS = "/api/saes/attachments"


class SettingsReadError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class Attachment:
    sae_id: str
    layer: int


def _data(body: Any, what: str) -> Any:
    if not isinstance(body, dict) or "data" not in body:
        raise ProtocolUnsupported(f"miLLM's {what} did not answer {{success, data}}")
    return body["data"]


def list_profiles(caller: EndpointCaller) -> list[dict[str, Any]]:
    response = caller.raw("GET", PROFILES)
    if response.status != 200:
        raise SettingsReadError(
            "PROFILES_UNREADABLE",
            f"miLLM answered {response.status} to GET {PROFILES}.",
            {"status": response.status},
        )
    data = _data(response.body, PROFILES)
    profiles = data.get("profiles") if isinstance(data, dict) else None
    if not isinstance(profiles, list):
        raise ProtocolUnsupported("miLLM's profile list has no 'profiles' array")
    return [p for p in profiles if isinstance(p, dict)]


def get_profile(caller: EndpointCaller, profile_id: str) -> dict[str, Any] | None:
    """One profile by id, or None when miLLM answers 404."""
    response = caller.raw("GET", f"{PROFILES}/{profile_id}")
    if response.status == 404:
        return None
    if response.status != 200:
        raise SettingsReadError(
            "PROFILES_UNREADABLE",
            f"miLLM answered {response.status} to GET {PROFILES}/{{id}}.",
            {"status": response.status},
        )
    data = _data(response.body, f"{PROFILES}/{{id}}")
    if not isinstance(data, dict):
        raise ProtocolUnsupported("miLLM's profile body is not an object")
    return data


def profile_by_name(caller: EndpointCaller, name: str) -> dict[str, Any] | None:
    """The profile named ``name`` (miLLM's per-request ``profile`` names a profile by name)."""
    matches = [p for p in list_profiles(caller) if p.get("name") == name]
    if not matches:
        return None
    full = get_profile(caller, str(matches[0]["id"]))
    return full


def attachments(caller: EndpointCaller) -> list[Attachment]:
    try:
        response = caller.raw("GET", ATTACHMENTS)
    except EndpointCallError:
        raise
    if response.status != 200:
        raise SettingsReadError(
            "ATTACHMENTS_UNREADABLE",
            f"miLLM answered {response.status} to GET {ATTACHMENTS}.",
            {"status": response.status},
        )
    data = _data(response.body, ATTACHMENTS)
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise ProtocolUnsupported("miLLM's attachment status has no 'entries' array")
    out: list[Attachment] = []
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("sae_id"), str):
            layer = entry.get("layer")
            if isinstance(layer, int) and not isinstance(layer, bool):
                out.append(Attachment(entry["sae_id"], layer))
    return out
