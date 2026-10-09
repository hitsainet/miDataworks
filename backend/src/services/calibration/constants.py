"""Every threshold and default of feature 006, named once (FTDD 006 section 6.1).

C3 and the audit bounds are operator DECISIONS (C3, R-03.40, T-26), not settings, so none of them
is an environment variable. ``tests/unit/calibration/test_constants_pinned.py`` pins each value,
so a change is deliberate and reviewed.
"""

from __future__ import annotations

#: C3: with no operator target and no valid ceiling, the AUROC 95% interval's LOWER bound must
#: reach this (compared with ``>=``, X-03).
C3_DEFAULT_LOWER_BOUND = 0.70

#: The prototype's bootstrap settings (``scripts/validate_judge.py``).
BOOTSTRAP_RESAMPLES = 2000
#: Random held-out draws for the rater ceiling (FR-006.8; the prototype's 20).
RATER_DRAWS = 20
#: A row needs at least this many ratings to enter the ceiling (FR-006.8).
MIN_RATINGS_FOR_CEILING = 3
#: Equal-width reliability bins (FR-006.11) and the row count under which a bin is greyed.
RELIABILITY_BINS = 10
RELIABILITY_MIN_ROWS = 30
#: A held-out rater at or above this AUROC is part of its own consensus (FR-006.15).
CIRCULARITY_LIMIT = 0.99
#: A fixed-position draw is only detectable with enough rows to make chance negligible.
FIXED_POSITION_MIN_ROWS = 20
#: Label-permutation null (FR-006.15).
PERMUTATIONS = 200
#: Row alignment: the recomputed AUROC must equal the reported one (FTID 006 section 7.6).
ROW_ALIGNMENT_TOLERANCE = 1e-12

#: Audit sample bounds (R-03.40, T-26).
AUDIT_MIN = 50
AUDIT_MAX = 100
AUDIT_DEFAULT = 100

#: Seeds (FTID 006 IQ4). The first two are the prototype's; every one is recorded on the record.
SEED_AUROC = 20261004
SEED_PAIRS = 20261005
SEED_CEILING_DRAWS = 20261005
SEED_CEILING_BOOTSTRAP = 20261006
SEED_PERMUTATION = 20261007

#: An external candidate's payload cap and a candidate batch's size cap (FTDD 006 section 5.3).
CANDIDATE_BATCH_MAX = 500

#: The resolver's identifier, shared with 008's projection (FR-006.25).
RESOLVER_ID = "dw.effective-label/v1"
MAPPING_SCHEMA = "dw.calibration-mapping/v1"

#: Recorded on every record's settings so a later change of method is visible.
CODE_VERSION = "dw.calibration/v1"
