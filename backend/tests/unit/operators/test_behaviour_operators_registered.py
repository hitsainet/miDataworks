"""Feature 017's behaviour operators are in the LIVE registry (FTASKS 4.3; ADR-021).

Read from ``OperatorRegistry.build()``, the registry a worker loads, never from the module: each
behaviour operator is runnable at its version with its manifest, kind and queue. Extended by 6.1,
6.5 and 8.1 as their operators land. Removing the ``BEHAVIOUR_OPERATORS`` line from
``operators/native/registrations.py`` turns this red (control 4.3).
"""

from __future__ import annotations

from src.operators.registry import OperatorRegistry

#: name -> (version, kind, scope): what slice 2 ships.
EXPECTED = {"carrier_plan": ("1.0.0", "mapper", "dataset")}


def test_every_behaviour_operator_is_live_and_runnable() -> None:
    reg = OperatorRegistry.build(catalogues=(), entry_points=())
    for name, (version, kind, scope) in EXPECTED.items():
        assert reg.current_version(name) == version, name
        entry = reg.entry(name, version)
        assert entry.manifest is not None, entry.error
        assert (entry.manifest.kind, entry.manifest.scope) == (kind, scope), name
        assert entry.manifest.resources.queue == "curation", name
        assert reg.implementation(entry) is not None
        required = set(entry.manifest.params_schema["required"])
        assert {"definition", "vocabulary", "rows", "templated_ceiling"} <= required
