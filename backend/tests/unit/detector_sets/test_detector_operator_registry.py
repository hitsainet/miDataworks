"""009's operators are in the LIVE registry, built the production way (FTASKS 11.5). Deleting the
``DETECTOR_OPERATORS`` line in ``operators/native/registrations.py`` turns this red."""

from __future__ import annotations

from src.operators.registry import OperatorRegistry


def test_the_detector_operators_are_allowed_in_the_production_registry() -> None:
    production = OperatorRegistry.build(catalogues=(), entry_points=())
    states = {(e.name, e.version): s for e, s in production.entries()}
    assert states.get(("hard_negative_miner", "1")) == "allowed"
    assert states.get(("feature_filter", "1")) == "allowed"
    # The minimal-pair generator is a chain; its two operators are registered (decision 2026-10-07).
    assert states.get(("minimal_pair_scope", "1")) == "allowed"
    assert states.get(("minimal_pair_join", "1")) == "allowed"
    # What became label-run protocols is NOT an operator (operator decision 2026-10-07).
    names = {name for name, _ in states}
    assert "probe_verdict_labeler" not in names and "feature_tagger" not in names
    assert "minimal_pair_generator" not in names
