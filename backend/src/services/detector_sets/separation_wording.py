"""D-9 "Wording alone": how well the words alone separate the labels (015 FR-015.15, D55).

Pure: texts, labels and optional groups in, one figure out. ``set_service`` reads the rows and
caches the result; ``checks._d9`` owns the rule and the words.

Method (FTDD 015 section 9; miDataworks memory ``measure-wording-before-publishing-pairs``):
TF-IDF over word 1- and 2-grams (``min_df=2``, at most 50,000 features), refitted inside each of five
folds, feeding a logistic regression (``liblinear``, ``C=1.0``). The folds are grouped by the pair
column when the role has one (a pair never straddles train and test), otherwise stratified. The
out-of-fold scores give one cross-validated AUROC. Text is read only in memory and never logged.
The labels are the column's own values read through the role's mapping; 006 review overrides are not
applied (D-9 measures the wording against the labels the data carries; FTID 015 I11).
"""

from __future__ import annotations

from typing import Any

from ..curation import text_baseline as _fitter
from ..curation.text_baseline import FOLDS, MIN_PER_CLASS, SEED, WordingResult

#: v2 (017 OQ-017-3 (a)): the figure comes from the ONE fitter in ``curation/text_baseline``, so
#: every cached D-9 figure is computed once more (the model's settings are D-9's own, unchanged).
MEASURE_VERSION = "dw.wording/v2"
#: The leave-one-value-out D-9 figure is cached under its own measure (FR-017.45).
GROUPED_MEASURE_VERSION = "dw.wording-loo/v1"

__all__ = ["FOLDS", "MIN_PER_CLASS", "SEED", "WordingResult", "as_text", "wording_auroc"]


def as_text(value: Any) -> str:
    """A cell as words: a chat (list of ``{role, content}``) is its contents joined."""
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(
            str(m.get("content", "")) if isinstance(m, dict) else str(m) for m in value
        )
    return str(value)


def _fit_predict(train_texts: list[str], train_y: list[int], test_texts: list[str]) -> list[float]:
    """One fold, through the one fitter (a seam the D-9 tests spy on)."""
    return _fitter.fit_predict(train_texts, train_y, test_texts)


def wording_auroc(
    texts: Any,
    labels: Any,
    groups: Any = None,
    *,
    kind: Any = None,
    seed: int = SEED,
    folds: int = FOLDS,
) -> WordingResult:
    """D-9's figure: :func:`curation.text_baseline.wording_auroc` with this module's fold."""
    return _fitter.wording_auroc(
        texts, labels, groups, kind=kind, seed=seed, folds=folds, predict=_fit_predict
    )
