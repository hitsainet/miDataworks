"""Manifest keys and values, never comments (feature 020; FR-020.11; FTASKS 8.5)."""

from __future__ import annotations

import json

import pytest
import yaml

from tests.support import legacy_identifiers as li
from tests.unit.brand import scan


def _reference(export: li.Export, deny: str) -> int:
    """Each parsed document serialised whole (JSON) and searched as one text."""
    count = 0
    for path in scan.tracked("k8s/**/*.yaml", ".github/workflows/*.yml"):
        for doc in yaml.safe_load_all(path.read_text()):
            text = json.dumps(doc, ensure_ascii=False, default=str)
            count += len(li.unallowed_hits(text, export, deny))
    return count


def test_manifest_values_report_count_equals_reference() -> None:
    export, deny = li.load(), scan.policy()["deny"]
    hits = scan.manifest_hits(export, deny)
    assert scan.record("manifests", hits) == _reference(export, deny)


def test_manifest_values_have_no_unallowed_old_name() -> None:
    export, deny = li.load(), scan.policy()["deny"]
    scan.judge("manifests", scan.manifest_hits(export, deny))


def test_a_manifest_key_is_read_as_it_stands(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ConfigMap key or value is the configuration name itself: never a reported identifier,
    in a code span or not. Through ``scan.manifest_hits``, the function the verdict uses."""
    export, deny = li.load(), scan.policy()["deny"]
    planted = [("k8s/x.yaml", "MILLM_FOO"), ("k8s/x.yaml", "`mistudio_foo_bar`")]
    monkeypatch.setattr(scan, "manifests", lambda paths=None: planted)
    assert scan.manifest_hits(export, deny) == ["k8s/x.yaml: MILLM", "k8s/x.yaml: mistudio"]
