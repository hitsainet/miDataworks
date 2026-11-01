"""Feature 009's native detector operators (FTDD 009 section 6.3; FTASKS 11.5, 14.x, 16.3).

``DETECTOR_OPERATORS`` is THE list, imported once by 003's ``operators/native/registrations.py``.

Of FTDD 6.3's five operators, two are operators here and two became label-run protocols
(operator decision 2026-10-07, extended to feature tagging by the same reasoning — 003's
operators get no database session and cannot write a label run):

- ``probe_verdict_labeler@1`` -> 005's ``millm_probe_score`` protocol (run kind ``probe_verdict``);
- ``feature_tagger@1`` -> 005's ``millm_sae_features`` protocol (run kind ``feature_tag``);
- ``hard_negative_miner@1`` and ``feature_filter@1`` stay operators: they READ a finished run's
  published ``labels.parquet`` through a ``label_run`` binding, as 005's ``threshold_labeler``
  does, so a build never calls miLLM live (002 FR-002.2);
- ``minimal_pair_generator@1`` became a CHAIN (operator decision 2026-10-07; FTASKS 15.0): a 007
  generation run in ``minimal_pairs`` mode, ``minimal_pair_scope@1``, a 005 judge run and
  ``minimal_pair_join@1``, started by ``services/detector_sets/minimal_pair_chain.py``.
"""

from __future__ import annotations

from .feature_filter import FeatureFilter
from .hard_negative_miner import HardNegativeMiner
from .minimal_pairs import MinimalPairJoin, MinimalPairScope

DETECTOR_OPERATORS: tuple[type, ...] = (
    HardNegativeMiner,
    FeatureFilter,
    MinimalPairScope,
    MinimalPairJoin,
)
