"""The P7 check: nothing in miDataworks still points at a previous sibling address (feature 020;
FR-020.27; FTID §10.4; FTASKS 19.4, 19.10).

Before the siblings' old in-cluster addresses are deleted (ENH-009 P7), every one of these must hold,
read from miDataworks' own API:

- no endpoint role has ``uses_previous_address: true`` (FR-020.14: the role still calls the old
  address, which stops answering when the alias goes);
- no runtime override row holds a value whose origin is a previous origin (FR-020.6);
- ``/api/health`` reports ``address_source: "env"`` for both siblings (not an override, not unset,
  not an old variable name).

It also refuses to pass when a sibling lists NO previous origin: the role flag and the override test
are both computed against that list, so with it empty they find nothing and the check would pass for
the wrong reason (the previous lists stay after P7, FR-020.27, PD-5).

Read-only: three GETs. Standard library plus ``httpx``, so it runs from any checkout without the
backend's settings. Exit status 0 when ready, 1 with the reasons, 2 when the API could not be read.
Run from the repository root::

    python backend/scripts/p7_check.py [--api http://k8s-midataworks.hitsai.local] [--host NAME]

``--host`` sends that Host header (reach the ingress by its address when the name does not resolve
on this machine).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

#: The siblings by their frozen health keys (register DR-36): miServer, miWorkbench.
SIBLINGS: dict[str, str] = {"millm": "miServer", "mistudio": "miWorkbench"}
DEFAULT_API = "http://k8s-midataworks.hitsai.local"
_DEFAULT_PORTS = {"http": 80, "https": 443}


def origin(value: object) -> str | None:
    """``scheme://host:port`` of a URL (port made explicit), else None. Mirrors the backend's
    ``key_binding.url_origin`` for the values this check compares."""
    if not isinstance(value, str) or "://" not in value:
        return None
    try:
        parts = urlsplit(value.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not parts.hostname:
        return None
    return f"{scheme}://{parts.hostname.lower()}:{port or _DEFAULT_PORTS[scheme]}"


def _rows(answer: Any) -> list[dict[str, Any]]:
    """A list answer, or a paged one (``{"items": [...]}``)."""
    if isinstance(answer, dict) and isinstance(answer.get("items"), list):
        answer = answer["items"]
    if not isinstance(answer, list):
        raise ValueError(f"expected a list, got {type(answer).__name__}")
    return [row for row in answer if isinstance(row, dict)]


def p7_readiness(health: dict[str, Any], roles: Any, overrides: Any) -> list[str]:
    """Every reason the old addresses cannot go yet; empty when they can.

    ``health`` is ``GET /api/health``; ``roles`` is ``GET /api/v1/endpoint-roles``; ``overrides``
    is ``GET /api/v1/settings`` (rows whose ``source`` is not ``override`` are ignored). A field the
    check needs and does not find is itself a reason: it never passes on a missing answer.
    """
    reasons: list[str] = []
    dependencies = health.get("dependencies") if isinstance(health, dict) else None
    if not isinstance(dependencies, dict):
        return ["health: no dependencies block; nothing could be checked"]
    previous: set[str] = set()
    for key, name in SIBLINGS.items():
        block = dependencies.get(key)
        if not isinstance(block, dict):
            reasons.append(f"health: no dependencies.{key} block ({name})")
            continue
        source = block.get("address_source")
        if source != "env":
            reasons.append(
                f"{name}: address_source is {source!r}, not 'env' (dependencies.{key}); "
                "the current address must come from the new variable in the ConfigMap"
            )
        listed = block.get("previous_origins")
        if not isinstance(listed, list) or not listed:
            reasons.append(
                f"{name}: no previous origins listed (dependencies.{key}.previous_origins); "
                "nothing on an old address could be found, so this check would pass vacuously"
            )
            continue
        previous |= {o for o in (origin(v) for v in listed) if o is not None}
    for role in _rows(roles):
        name = role.get("role", "?")
        if "uses_previous_address" not in role:
            reasons.append(f"endpoint role {name}: the answer has no uses_previous_address")
        elif role["uses_previous_address"]:
            reasons.append(
                f"endpoint role {name} is on a previous address {role.get('base_url')!r}; "
                f"re-point it to {role.get('current_address')!r}"
            )
    for row in _rows(overrides):
        if row.get("source") != "override":
            continue
        found = origin(row.get("value"))
        if found is not None and found in previous:
            reasons.append(
                f"runtime override {row.get('key')!r} holds a previous origin {found}; reset it "
                "or set the current address"
            )
    return reasons


def read_api(api: str, host: str | None = None, timeout: float = 30.0) -> dict[str, Any]:
    """The three answers the check reads. Raises ``httpx.HTTPError`` when one cannot be read."""
    headers = {"Host": host} if host else {}
    with httpx.Client(base_url=api.rstrip("/"), headers=headers, timeout=timeout) as client:
        answers: dict[str, Any] = {}
        for name, path in (
            ("health", "/api/health"),
            ("roles", "/api/v1/endpoint-roles"),
            ("overrides", "/api/v1/settings"),
        ):
            response = client.get(path)
            response.raise_for_status()
            answers[name] = response.json()
    return answers


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--api", default=DEFAULT_API, help="miDataworks API base URL")
    parser.add_argument("--host", default=None, help="Host header to send (optional)")
    args = parser.parse_args(argv)
    read_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        answers = read_api(args.api, args.host)
    except (httpx.HTTPError, ValueError) as exc:
        print(json.dumps({"read_at": read_at, "error": f"{type(exc).__name__}: {exc}"}))
        return 2
    reasons = p7_readiness(answers["health"], answers["roles"], answers["overrides"])
    dependencies = answers["health"].get("dependencies") or {}
    report = {
        "read_at": read_at,
        "api": args.api,
        "ready": not reasons,
        "reasons": reasons,
        "address_source": {
            name: (dependencies.get(key) or {}).get("address_source")
            for key, name in SIBLINGS.items()
        },
        "previous_origins": {
            name: (dependencies.get(key) or {}).get("previous_origins")
            for key, name in SIBLINGS.items()
        },
        "roles_checked": len(_rows(answers["roles"])),
        "overrides_checked": sum(
            1 for r in _rows(answers["overrides"]) if r.get("source") == "override"
        ),
    }
    print(json.dumps(report, indent=2))
    return 0 if not reasons else 1


if __name__ == "__main__":
    sys.exit(main())
