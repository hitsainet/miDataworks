"""Whether miWorkbench is configured, for the three 009 actions that cannot run without it.

miDataworks stands alone (operator principle, 2026-10-07): every feature works with no sibling
configured, and an integration-only action REFUSES naming the sibling it needs. A detector set
itself is standalone (build, check, publish its versions to the Hub); only the send to miWorkbench,
the results read-back and a reward mark talk to miWorkbench, and each calls :func:`require_mistudio`
before anything else, so a sibling-less call is a ``409 mistudio_not_configured``, never a 500, a
hang, or a row recorded against an empty miWorkbench address.
"""

from __future__ import annotations

from ...core.config import get_settings
from ...core.env_names import setting_name
from .errors import DetectorSetError

#: What a standalone operator does instead (data flows miDataworks -> Hugging Face -> miWorkbench).
STANDALONE_NEXT_STEP = (
    "Everything else in miDataworks works without miWorkbench: publish the versions to the Hugging "
    "Face Hub from Publish and export, and import them into miWorkbench from the Hub."
)


def require_mistudio(action: str) -> str:
    """The configured miWorkbench base URL, or ``mistudio_not_configured`` naming ``action``."""
    base_url = get_settings().mistudio_base_url
    if not base_url:
        raise DetectorSetError(
            "mistudio_not_configured",
            f"{action} needs miWorkbench, and no miWorkbench URL is configured "
            f"({setting_name('mistudio_base_url')}). "
            f"Set it in the deployment configuration to use it. {STANDALONE_NEXT_STEP}",
            {
                "sibling": "miWorkbench",
                "setting": setting_name("mistudio_base_url"),
                "action": action,
            },
        )
    return base_url
