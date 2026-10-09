"""The committed engine catalogues load into the registry as designed (FR-003.14, FR-003.15)."""

from __future__ import annotations

import json
from pathlib import Path

from src.operators.registry import DJ_CATALOGUE, OperatorRegistry

REPO = Path(__file__).resolve().parents[4]


def test_the_datajuicer_subset_copy_is_byte_identical() -> None:
    """FTID 003 I-3, checked in the backend job too (the Data-Juicer job checks it again)."""
    backend = REPO / "backend" / "src" / "operators" / "schema_subset.py"
    assert (REPO / "datajuicer" / "schema_subset.py").read_bytes() == backend.read_bytes()


def test_every_offered_datajuicer_operator_is_allowed_with_its_statistic() -> None:
    document = json.loads(DJ_CATALOGUE.read_text())
    reg = OperatorRegistry.build(
        native=(), catalogues=((DJ_CATALOGUE, "datajuicer"),), entry_points=()
    )
    states = {e.name: s for e, s in reg.entries()}
    offered = {f"dj_{o['op_name']}" for o in document["operators"]}
    assert offered and all(states[name] == "allowed" for name in offered)
    for item in document["operators"]:
        manifest = item["manifest"]
        assert manifest["provider_version"] == document["provider_version"]
        assert manifest["resources"]["queue"] == "datajuicer"
        if manifest["kind"] == "filter":
            assert item["stats_key"] and manifest["thresholds"], item["op_name"]
    assert document["total_ops"] == len(document["operators"]) + len(document["rejected"])
    assert all(r["reason"] for r in document["rejected"])
    assert "document_minhash_deduplicator" in {r["op_name"] for r in document["rejected"]}


def test_the_datajuicer_pin_matches_the_catalogue() -> None:
    pins = (REPO / "datajuicer" / "requirements.txt").read_text()
    version = json.loads(DJ_CATALOGUE.read_text())["provider_version"]
    assert f"py-data-juicer=={version}" in pins
