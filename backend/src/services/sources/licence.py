"""The single licence mapping (FR-001.26, FR-001.27; 001 FTID section 3.3).

``licence_from_hub(card_license, tags) -> (raw, display, origin)``. The card's ``license`` wins (a
string, or a list joined with ", "); otherwise ``license:<id>`` tags; otherwise nothing. The display
is ``"not stated"`` when the chosen raw value is absent, empty or ``unknown`` (any case) —
Humicroedit's case: its card says ``unknown``, which is not a licence. The raw value is kept
exactly as the Hub returned it. Uploads start "not stated".
"""

from __future__ import annotations

from typing import Any

from ...models.source_enums import LICENCE_NOT_STATED, LicenceOrigin


def _shown(raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, list):
        parts = [str(x).strip() for x in raw if str(x).strip()]
        text = ", ".join(parts)
    else:
        text = str(raw).strip()
    if not text or text.lower() == "unknown":
        return None
    return text


def licence_from_hub(card_license: Any, tags: list[str] | None) -> tuple[Any, str, str]:
    if card_license not in (None, "", []):
        return (
            card_license,
            _shown(card_license) or LICENCE_NOT_STATED,
            LicenceOrigin.CARD_DATA.value,
        )
    from_tags = [t.split(":", 1)[1] for t in (tags or []) if t.startswith("license:")]
    if from_tags:
        raw: Any = from_tags[0] if len(from_tags) == 1 else from_tags
        return raw, _shown(raw) or LICENCE_NOT_STATED, LicenceOrigin.TAGS.value
    return None, LICENCE_NOT_STATED, LicenceOrigin.NONE.value
