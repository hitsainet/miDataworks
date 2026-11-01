"""The text baseline of the shortcut audit (PII run G7, 2026-10-20).

The audit scores METADATA columns; it never looks at the text, so the check that matters most
for a text detector ran locally on a downloaded copy. The miDataworks memory records why it
matters: synthetic pairs passed every miDataworks check at bag-of-words AUROC 0.9987. Asked for
(``text_baseline: true``), the audit also reports, on seeded stratified folds (the audit's fold
count and seed, rows in row-key order):

- ``tfidf_logreg``: TF-IDF (word 1-2 grams) + logistic regression, out-of-fold AUROC;
- ``length_only``: logistic regression on log(1 + characters), out-of-fold AUROC;
- ``tfidf_permuted_control``: the TF-IDF model on seeded permuted labels (should read about 0.5).

AUROC is binary for two classes and macro one-vs-rest above that. Inputs over
``CURATION_TEXT_BASELINE_MAX_ROWS`` are a seeded stratified sample, said in the result.
scikit-learn is already a backend dependency (the audit's folds use it).

**One fitter (017 OQ-017-3 (a), FR-017.45; FTDD §6.2).** :func:`text_model` is the ONE TF-IDF +
logistic regression this app fits on wording (word 1-2 grams, ``min_df=2``, at most 50,000
features; ``liblinear``, ``C=1.0``): the audit's figure and 015's D-9 (``detector_sets/
separation_wording``, now a thin caller) cannot drift apart. :func:`wording_auroc` takes three
fold kinds: ``stratified``, ``grouped`` (a pair column: a pair never straddles train and test)
and ``leave_one_value_out`` (each value of a column, e.g. ``genre``, held out once: the figure for
text the detector has never seen the genre of). With ``group_folds_by`` the audit reports that
grouped figure beside the stratified one; with a reference version, both are scored at the same
n (the smaller side's), stratified sampling, one seed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import pyarrow as pa

from .shortcut_rules import fold_ids
from .text_stats import as_text

#: At or above this, the wording alone separates the labels (the memory note's 0.96 reference).
WORDING_WARN_AUROC = 0.95
#: :func:`wording_auroc`: fewer rows of a class than this and nothing is computed.
MIN_PER_CLASS = 20
FOLDS = 5
SEED = 0
FoldKind = Literal["stratified", "grouped", "leave_one_value_out"]


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
    kind: str = "stratified"


def text_model() -> tuple[Any, Any]:
    """The ONE wording model: (vectoriser, classifier), fresh for each fold."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression

    return (
        TfidfVectorizer(ngram_range=(1, 2), min_df=2, max_features=50_000),
        LogisticRegression(solver="liblinear", C=1.0),
    )


def fit_predict(train_texts: list[str], train_y: list[int], test_texts: list[str]) -> list[float]:
    """One fold of a binary wording figure: fit on the training part only, score the held-out
    part (the positive class's probability)."""
    vectorizer, model = text_model()
    model.fit(vectorizer.fit_transform(train_texts), train_y)
    return [float(p) for p in model.predict_proba(vectorizer.transform(test_texts))[:, 1]]


def splitter(kind: str, folds: int, seed: int) -> Any:
    from sklearn.model_selection import LeaveOneGroupOut, StratifiedGroupKFold, StratifiedKFold

    if kind == "leave_one_value_out":
        return LeaveOneGroupOut()
    if kind == "grouped":
        return StratifiedGroupKFold(n_splits=folds, shuffle=True, random_state=seed)
    return StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed)


def wording_auroc(
    texts: Sequence[str],
    labels: Sequence[int],
    groups: Sequence[str] | None = None,
    *,
    kind: FoldKind | None = None,
    seed: int = SEED,
    folds: int = FOLDS,
    predict: Any = None,
) -> WordingResult:
    """The cross-validated AUROC of the one wording model; ``labels`` are 1 (positive) / 0.
    ``kind`` defaults to ``grouped`` with ``groups`` and ``stratified`` without; for
    ``leave_one_value_out`` the groups are the column's values (each held out once, so the
    folds are as many as the values). ``predict`` replaces :func:`fit_predict` (a test's spy)."""
    from sklearn.metrics import roc_auc_score

    fit = predict or fit_predict
    kind = kind or ("grouped" if groups is not None else "stratified")
    n_pos = sum(1 for y in labels if y == 1)
    n_neg = len(labels) - n_pos
    grouped = groups is not None
    n_folds = len(set(groups)) if kind == "leave_one_value_out" and groups is not None else folds
    base: dict[str, Any] = {
        "n": len(labels),
        "n_positive": n_pos,
        "n_negative": n_neg,
        "folds": n_folds,
        "grouped": grouped,
        "kind": kind,
    }
    if min(n_pos, n_neg) < MIN_PER_CLASS:
        return WordingResult(**base, auroc=None, reason="too_few_rows")
    if groups is None and kind != "stratified":
        raise ValueError(f"fold kind {kind!r} needs groups")
    if groups is not None and kind == "leave_one_value_out" and len(set(groups)) < 2:
        return WordingResult(**base, auroc=None, reason="too_few_groups")
    if groups is not None and kind == "grouped" and len(set(groups)) < folds:
        return WordingResult(**base, auroc=None, reason="too_few_groups")
    texts = list(texts)
    y = list(labels)
    scores = [0.0] * len(y)
    split = splitter(kind, folds, seed)
    args = (texts, y, list(groups)) if groups is not None else (texts, y)
    for train_idx, test_idx in split.split(*args):
        train_y = [y[i] for i in train_idx]
        if len(set(train_y)) < 2:
            # a value holding one class only, left as the whole training part: nothing to fit
            return WordingResult(**base, auroc=None, reason="one_class_in_training")
        predicted = fit([texts[i] for i in train_idx], train_y, [texts[i] for i in test_idx])
        for i, p in zip(test_idx, predicted, strict=True):
            scores[i] = p
    return WordingResult(**base, auroc=round(float(roc_auc_score(y, scores)), 4), reason=None)


def equal_n(
    texts: Sequence[str], labels: Sequence[int], n: int, seed: int
) -> tuple[list[str], list[int]]:
    """A seeded stratified sample of ``n`` rows (all of them when there are no more)."""
    from sklearn.model_selection import train_test_split

    if n >= len(texts):
        return list(texts), list(labels)
    keep, _ = train_test_split(
        np.arange(len(texts)), train_size=n, stratify=list(labels), random_state=seed
    )
    keep = np.sort(keep)
    return [texts[i] for i in keep], [int(labels[i]) for i in keep]


def _auroc(y: np.ndarray, proba: np.ndarray, k: int) -> float:
    from sklearn.metrics import roc_auc_score

    if k == 2:
        return float(roc_auc_score(y, proba[:, 1]))
    return float(roc_auc_score(y, proba, multi_class="ovr", average="macro", labels=list(range(k))))


def _oof(features: Any, y: np.ndarray, folds: np.ndarray, k: int, fit: Any) -> np.ndarray:
    proba = np.zeros((len(y), k))
    for fold in sorted(set(folds.tolist())):
        train, test = folds != fold, folds == fold
        model = fit(features, train, y)
        proba[np.ix_(test, list(model.classes_))] = model.predict_proba(features[test])
    return proba


def text_baseline(
    table: pa.Table,
    content: Sequence[str],
    y: np.ndarray,
    classes: Sequence[str],
    *,
    folds: int,
    seed: int,
    max_rows: int,
    group_by: str | None = None,
    reference: tuple[str, pa.Table, np.ndarray, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """``group_by`` (017 FR-017.45): also the leave-one-value-out figure over that column, beside
    the stratified one. ``reference`` = (version id, table, labels, content columns): the
    reference's figure and this version's, both at the same n."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import train_test_split

    k = len(classes)
    columns = [c for c in content if c in table.schema.names]
    if not columns:
        return {"computed": False, "reason": "The version has no content column to read."}
    values = [table.column(c).to_pylist() for c in columns]
    texts = np.array(["\n".join(as_text(v) for v in row) for row in zip(*values, strict=True)])
    group_values: np.ndarray | None = None
    if group_by is not None:
        group_values = np.array([str(v) for v in table.column(group_by).to_pylist()], dtype=object)
    n = len(texts)
    sampled = n > max_rows
    if sampled:
        keep, _ = train_test_split(np.arange(n), train_size=max_rows, stratify=y, random_state=seed)
        keep = np.sort(keep)
        texts, y = texts[keep], y[keep]
        if group_values is not None:
            group_values = group_values[keep]
    fold_index = fold_ids(y, folds, seed)

    def fit_text(x: Any, train: np.ndarray, labels: np.ndarray) -> Any:
        vectoriser, model = text_model()  # the one fitter (OQ-017-3 (a))
        model.fit(vectoriser.fit_transform(x[train]), labels[train])

        class Fitted:
            classes_ = model.classes_

            @staticmethod
            def predict_proba(rows: Any) -> np.ndarray:
                result: np.ndarray = model.predict_proba(vectoriser.transform(rows))
                return result

        return Fitted()

    def fit_length(x: Any, train: np.ndarray, labels: np.ndarray) -> Any:
        return LogisticRegression(max_iter=1000).fit(x[train], labels[train])

    try:
        tfidf = _auroc(y, _oof(texts, y, fold_index, k, fit_text), k)
        rng = np.random.default_rng(seed)
        permuted = rng.permutation(y)
        control = _auroc(permuted, _oof(texts, permuted, fold_index, k, fit_text), k)
    except ValueError as exc:  # an empty vocabulary (every text empty or unique words only)
        return {"computed": False, "reason": f"TF-IDF could not be fitted: {exc}"}
    lengths = np.log1p(np.array([len(t) for t in texts], dtype=np.float64)).reshape(-1, 1)
    length = _auroc(y, _oof(lengths, y, fold_index, k, fit_length), k)
    worst = max(tfidf, length)
    extra: dict[str, Any] = {}
    if group_by is not None and group_values is not None:
        extra["grouped"] = _grouped(texts, y, group_values, group_by, k, seed)
    if reference is not None:
        extra["reference"] = _reference(texts, y, k, reference, seed)
    return {
        "computed": True,
        "content_columns": columns,
        "n_rows": len(texts),
        "sampled": sampled,
        "sample_note": (
            f"A seeded stratified sample of {max_rows:,} of {n:,} rows "
            "(CURATION_TEXT_BASELINE_MAX_ROWS)."
            if sampled
            else None
        ),
        "folds": folds,
        "metric": "AUROC" if k == 2 else "macro one-vs-rest AUROC",
        "tfidf_logreg": round(tfidf, 4),
        "length_only": round(length, 4),
        "tfidf_permuted_control": round(control, 4),
        "wording_separates_labels": worst >= WORDING_WARN_AUROC,
        "interpretation": (
            f"The text alone predicts the label at AUROC {worst:.3f} on held-out folds: a "
            "detector trained here may learn wording or length rather than the concept. Compare "
            "with a reference set of the real concept at equal n before publishing."
            if worst >= WORDING_WARN_AUROC
            else f"The best text baseline reads AUROC {worst:.3f} on held-out folds."
        ),
        **extra,
    }


def _grouped(
    texts: np.ndarray, y: np.ndarray, groups: np.ndarray, by: str, k: int, seed: int
) -> dict[str, Any]:
    """The leave-one-value-out figure over ``by`` (FR-017.45): binary labels only."""
    if k != 2:
        return {"by": by, "computed": False, "reason": "binary labels only"}
    result = wording_auroc(
        list(texts), [int(v) for v in y], list(groups), kind="leave_one_value_out", seed=seed
    )
    return {
        "by": by,
        "computed": result.auroc is not None,
        "kind": result.kind,
        "values": result.folds,
        "n": result.n,
        "tfidf_logreg": result.auroc,
        "reason": result.reason,
    }


def _reference(
    texts: np.ndarray,
    y: np.ndarray,
    k: int,
    reference: tuple[str, pa.Table, np.ndarray, Sequence[str]],
    seed: int,
) -> dict[str, Any]:
    """This version's and the reference's stratified figures at the SAME n (the smaller)."""
    ref_id, ref_table, ref_y, content = reference
    out: dict[str, Any] = {"version_id": ref_id}
    if k != 2 or len(set(ref_y.tolist())) != 2:
        return {**out, "computed": False, "reason": "binary labels on both sides only"}
    columns = [c for c in content if c in ref_table.schema.names]
    if not columns:
        return {**out, "computed": False, "reason": "the reference has no content column"}
    ref_values = [ref_table.column(c).to_pylist() for c in columns]
    ref_texts = ["\n".join(as_text(v) for v in row) for row in zip(*ref_values, strict=True)]
    n = min(len(texts), len(ref_texts))
    mine_t, mine_y = equal_n(list(texts), [int(v) for v in y], n, seed)
    ref_t, ref_l = equal_n(ref_texts, [int(v) for v in ref_y], n, seed)
    mine = wording_auroc(mine_t, mine_y, seed=seed)
    ref = wording_auroc(ref_t, ref_l, seed=seed)
    return {
        **out,
        "computed": mine.auroc is not None and ref.auroc is not None,
        "n": n,
        "tfidf_logreg": mine.auroc,
        "reference_tfidf_logreg": ref.auroc,
        "reason": mine.reason or ref.reason,
    }
