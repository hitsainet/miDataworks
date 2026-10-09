"""Feature 006's decisions, pinned (006 FTASKS 3.1). A change here must be deliberate: C3 is an
operator decision, the audit bounds are R-03.40 and T-26, the bootstrap settings the prototype's."""

from __future__ import annotations

from src.services.calibration import constants as c


def test_decisions_are_pinned() -> None:
    assert c.C3_DEFAULT_LOWER_BOUND == 0.70
    assert (c.AUDIT_MIN, c.AUDIT_MAX, c.AUDIT_DEFAULT) == (50, 100, 100)
    assert c.BOOTSTRAP_RESAMPLES == 2000
    assert c.RATER_DRAWS == 20
    assert c.RELIABILITY_BINS == 10
    assert c.CIRCULARITY_LIMIT == 0.99
    assert c.MIN_RATINGS_FOR_CEILING == 3
    assert c.CANDIDATE_BATCH_MAX == 500


def test_seeds_are_the_prototypes() -> None:
    assert c.SEED_AUROC == 20261004  # scripts/validate_judge.py
    assert c.SEED_PAIRS == 20261005  # scripts/paired_probe.py
    assert (c.SEED_CEILING_DRAWS, c.SEED_CEILING_BOOTSTRAP) == (20261005, 20261006)


def test_identifiers() -> None:
    assert c.RESOLVER_ID == "dw.effective-label/v1"
    assert c.MAPPING_SCHEMA == "dw.calibration-mapping/v1"
