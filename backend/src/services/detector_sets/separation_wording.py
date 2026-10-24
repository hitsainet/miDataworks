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

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

MEASURE_VERSION = "dw.wording/v1"
MIN_PER_CLASS = 20
FOLDS = 5
SEED = 0


@dataclass(frozen=True)
class WordingResult:
    n: int
    n_positive: int
    n_negative: int
    #: None when not computed (``reason`` says why); never read as a pass.
    auroc: float | None
    folds: int
    grouped: bool
    reason: str | None = None


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
    """One fold: fit on the training part only, score the held-out part."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression

    vectorizer = TfidfVectorizer(ngram_range=(1, 2), min_df=2, max_features=50_000)
    x_train = vectorizer.fit_transform(train_texts)
    model = LogisticRegression(solver="liblinear", C=1.0)
    model.fit(x_train, train_y)
    return [float(p) for p in model.predict_proba(vectorizer.transform(test_texts))[:, 1]]


def splitter(grouped: bool, folds: int, seed: int) -> Any:
    from sklearn.model_selection import StratifiedGroupKFold, StratifiedKFold

    if grouped:
        return StratifiedGroupKFold(n_splits=folds, shuffle=True, random_state=seed)
    return StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed)


def wording_auroc(
    texts: Sequence[str],
    labels: Sequence[int],
    groups: Sequence[str] | None = None,
    *,
    seed: int = SEED,
    folds: int = FOLDS,
) -> WordingResult:
    """The cross-validated AUROC of a bag-of-words classifier; ``labels`` are 1 (positive) / 0."""
    from sklearn.metrics import roc_auc_score

    n_pos = sum(1 for y in labels if y == 1)
    n_neg = len(labels) - n_pos
    grouped = groups is not None
    base = {"n": len(labels), "n_positive": n_pos, "n_negative": n_neg, "folds": folds}
    if min(n_pos, n_neg) < MIN_PER_CLASS:
        return WordingResult(**base, auroc=None, grouped=grouped, reason="too_few_rows")
    if groups is not None and len(set(groups)) < folds:
        return WordingResult(**base, auroc=None, grouped=grouped, reason="too_few_groups")
    texts = list(texts)
    y = list(labels)
    scores = [0.0] * len(y)
    split = splitter(grouped, folds, seed)
    args = (texts, y, list(groups)) if groups is not None else (texts, y)
    for train_idx, test_idx in split.split(*args):
        predicted = _fit_predict(
            [texts[i] for i in train_idx], [y[i] for i in train_idx], [texts[i] for i in test_idx]
        )
        for i, p in zip(test_idx, predicted, strict=True):
            scores[i] = p
    return WordingResult(
        **base, auroc=round(float(roc_auc_score(y, scores)), 4), grouped=grouped, reason=None
    )
