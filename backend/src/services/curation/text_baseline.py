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
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import pyarrow as pa

from .shortcut_rules import fold_ids
from .text_stats import as_text

#: At or above this, the wording alone separates the labels (the memory note's 0.96 reference).
WORDING_WARN_AUROC = 0.95


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
) -> dict[str, Any]:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import train_test_split

    k = len(classes)
    columns = [c for c in content if c in table.schema.names]
    if not columns:
        return {"computed": False, "reason": "The version has no content column to read."}
    values = [table.column(c).to_pylist() for c in columns]
    texts = np.array(["\n".join(as_text(v) for v in row) for row in zip(*values, strict=True)])
    n = len(texts)
    sampled = n > max_rows
    if sampled:
        keep, _ = train_test_split(np.arange(n), train_size=max_rows, stratify=y, random_state=seed)
        keep = np.sort(keep)
        texts, y = texts[keep], y[keep]
    fold_index = fold_ids(y, folds, seed)

    def fit_text(x: Any, train: np.ndarray, labels: np.ndarray) -> Any:
        vectoriser = TfidfVectorizer(
            ngram_range=(1, 2), min_df=2, max_features=50_000, sublinear_tf=True
        )
        model = LogisticRegression(max_iter=1000)
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
    }
