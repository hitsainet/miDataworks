"""An endpoint role's API key is bound to the server it was entered for (operator decision
2026-10-20, 005 FTASKS 12.3 follow-up).

A key is stored with the ORIGIN (scheme, host and port) of the role's base URL at the moment it was
entered. Two rules follow, and each is enforced in more than one place on purpose:

- **Settings** (``EndpointRoleService.update``): when a role's base URL moves to another origin and
  the same request does not supply a new key, the stored key is cleared, and the answer says so.
- **Every sender** (``EndpointCaller``, the loopback relay, the Data Designer hand-off, Fetch models,
  the resolver): a key is sent only to a URL whose origin equals the key's stored origin. A key with
  no stored origin is never sent.

Standard library only: the Data Designer image carries a copy of :func:`url_origin` in
``operators/data_designer/relay.py`` (it cannot import ``src.core``), and
``tests/unit/test_key_binding.py`` pins the two to the same answers.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

_DEFAULT_PORTS = {"http": 80, "https": 443}


def url_origin(url: str | None) -> str | None:
    """``scheme://host:port`` of ``url``, lower-cased with the default port made explicit, or None
    for no URL or one that names no host. ``https://A.example/v1`` → ``https://a.example:443``."""
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if not scheme or not host:
        return None
    if port is None:
        port = _DEFAULT_PORTS.get(scheme)
    if port is None:
        return None
    if ":" in host:  # an IPv6 literal keeps its brackets
        host = f"[{host}]"
    return f"{scheme}://{host}:{port}"


def same_origin(a: str | None, b: str | None) -> bool:
    origin_a, origin_b = url_origin(a), url_origin(b)
    return origin_a is not None and origin_a == origin_b


def key_for(url: str | None, api_key: str | None, key_origin: str | None) -> str | None:
    """The key to send to ``url``: ``api_key`` when its stored origin is ``url``'s origin, else None.

    A key with no stored origin is withheld: it was never bound to any server."""
    if not api_key:
        return None
    target = url_origin(url)
    if key_origin is None or target is None or key_origin != target:
        logger.warning(
            "endpoint.key_withheld target_origin=%s key_origin=%s "
            "(a role's key is sent only to the server it was entered for)",
            target,
            key_origin,
        )
        return None
    return api_key
