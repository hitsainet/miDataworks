"""The runtime-key labels an operator reads name the siblings by their new names (feature 020;
FR-020.18; FTASKS 15.1, control M43b).

The KEYS are frozen (``millm_base_url`` and the rest are Settings field names, PD-2, register entry
DR-18): only the label, the effect detail and the caution beside the editor change. The texts are
read from ``RUNTIME_KEYS`` itself, the registry the Environment card is served from, and through
the listing the card reads, so a label that the registry holds but the listing does not serve
fails too.
"""

from __future__ import annotations

import re

import pytest

from src.core import runtime_settings as rs

OLD_NAME = re.compile(r"(?i)mi(llm|studio)")
SIBLING_KEYS = {
    "millm_base_url": "miServer address",
    "millm_ui_url": "miServer admin screen address (as your browser reaches it)",
    "mistudio_base_url": "miWorkbench address",
}
SIBLING_WORDS = {
    "millm_lease_ttl_seconds": "miServer",
    "label_batch_poll_seconds": "miServer",
    "detector_send_poll_seconds": "miWorkbench",
    "detector_send_download_timeout_minutes": "miWorkbench",
    "mistudio_timeout_seconds": "miWorkbench",
}


def _texts(spec: rs.RuntimeKey) -> list[str]:
    return [spec.label, spec.effect_detail, spec.caution]


@pytest.mark.parametrize("key", sorted(SIBLING_KEYS))
def test_sibling_runtime_key_labels_name_new_siblings(key: str) -> None:
    assert rs.RUNTIME_KEYS[key].label == SIBLING_KEYS[key]


@pytest.mark.parametrize("key", sorted(SIBLING_WORDS))
def test_labels_that_mention_a_sibling_use_its_new_name(key: str) -> None:
    assert SIBLING_WORDS[key] in rs.RUNTIME_KEYS[key].label


def test_no_runtime_key_text_names_an_old_sibling() -> None:
    """Every key, not a hand-picked few: label, effect detail and caution."""
    assert len(rs.RUNTIME_KEYS) >= 26
    stale = {
        key: text
        for key, spec in rs.RUNTIME_KEYS.items()
        for text in _texts(spec)
        if OLD_NAME.search(text)
    }
    assert stale == {}


def test_no_restart_reason_names_an_old_sibling() -> None:
    assert rs.RESTART_KEYS
    assert {k: why for k, why in rs.RESTART_KEYS.items() if OLD_NAME.search(why)} == {}


def test_the_keys_themselves_are_frozen() -> None:
    """The copy pass renames what is read, never the key (PD-2)."""
    for key in (*SIBLING_KEYS, "millm_lease_ttl_seconds", "millm_steering_supported"):
        assert key in rs.RUNTIME_KEYS, key
    assert not [k for k in rs.RUNTIME_KEYS if k.startswith(("miserver_", "miworkbench_"))]
