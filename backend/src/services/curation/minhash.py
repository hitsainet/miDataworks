"""MinHash with locality-sensitive hashing (FR-004.13, 004.20; FTID 004 §7.5; P-18 lexical basis).

Pure. Shingles (word or character n-grams) are hashed with ``blake2b`` and reduced to 32 bits;
permutation ``i`` is ``((a_i * h + b_i) mod p) mod 2^32`` with the Mersenne prime ``p = 2^61 - 1`` and
``a_i, b_i`` drawn from the step seed. Keeping ``h, a, b < 2^32`` makes ``a*h + b < 2^64``, so the
product never overflows numpy's uint64 (FTID §7.5 named 64-bit shingle hashes; 32 bits is the
overflow-free form, recorded). Bands: ``b`` bands of ``r`` rows with ``b * r = permutations``, chosen
so the S-curve midpoint ``(1/b)^(1/r)`` is nearest the threshold, and recorded with every result.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

MERSENNE = (1 << 61) - 1
MASK32 = 0xFFFFFFFF
_WORD = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True)
class BandLayout:
    bands: int
    rows: int

    @property
    def midpoint(self) -> float:
        return float((1.0 / self.bands) ** (1.0 / self.rows))


def shingles(text: str, kind: str = "word", size: int = 5) -> np.ndarray:
    """32-bit hashes of the text's shingles; a text shorter than one shingle is one shingle."""
    if kind == "char":
        source = " ".join(text.lower().split())
        grams = [source[i : i + size] for i in range(max(1, len(source) - size + 1))]
    else:
        tokens = _WORD.findall(text.lower())
        grams = [" ".join(tokens[i : i + size]) for i in range(max(1, len(tokens) - size + 1))]
    unique = sorted(set(grams))
    return np.fromiter(
        (
            int.from_bytes(hashlib.blake2b(g.encode("utf-8"), digest_size=4).digest(), "big")
            for g in unique
        ),
        dtype=np.uint64,
        count=len(unique),
    )


def permutations(count: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    a = rng.integers(1, MASK32, size=count, dtype=np.uint64)
    b = rng.integers(0, MASK32, size=count, dtype=np.uint64)
    return a, b


def signature(hashes: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """The MinHash signature (one uint32 per permutation) of one row's shingle hashes."""
    if hashes.size == 0:
        return np.full(a.shape[0], MASK32, dtype=np.uint32)
    values = (np.outer(hashes, a) + b) % np.uint64(MERSENNE) & np.uint64(MASK32)
    result: np.ndarray = values.min(axis=0).astype(np.uint32)
    return result


def band_layout(permutation_count: int, threshold: float) -> BandLayout:
    """The (bands, rows) split whose S-curve midpoint is nearest ``threshold``."""
    options = [
        BandLayout(permutation_count // r, r)
        for r in range(1, permutation_count + 1)
        if permutation_count % r == 0
    ]
    return min(options, key=lambda o: (abs(o.midpoint - threshold), o.rows))


def band_keys(signatures: np.ndarray, layout: BandLayout) -> np.ndarray:
    """``(n, bands)`` keys: a polynomial hash (mod 2^64) of each band's ``rows`` values.

    A key collision only adds a candidate, and every candidate is verified against the full
    signatures, so the hash needs to be fast, not cryptographic.
    """
    n = signatures.shape[0]
    out = np.empty((n, layout.bands), dtype=np.uint64)
    base = np.uint64(0x100000001B3)
    with np.errstate(over="ignore"):
        for band in range(layout.bands):
            key = np.full(n, np.uint64(0xCBF29CE484222325), dtype=np.uint64)
            for j in range(layout.rows):
                column = signatures[:, band * layout.rows + j].astype(np.uint64)
                key = (key ^ column) * base
            out[:, band] = key
    return out


def jaccard_estimate(a: np.ndarray, b: np.ndarray) -> float:
    """Share of equal signature positions: the MinHash estimate of Jaccard similarity."""
    return float(np.mean(a == b))


def exact_jaccard(x: set[int], y: set[int]) -> float:
    return len(x & y) / len(x | y) if x or y else 1.0


def candidate_pairs(keys: np.ndarray) -> set[tuple[int, int]]:
    """Pairs sharing at least one band. Each bucket links its members to its first member (a star),
    so a large bucket costs ``len(bucket)`` comparisons, never its square."""
    pairs: set[tuple[int, int]] = set()
    for band in range(keys.shape[1]):
        column = keys[:, band]
        order = np.argsort(column, kind="stable")
        sorted_keys = column[order]
        starts = np.flatnonzero(np.r_[True, sorted_keys[1:] != sorted_keys[:-1]])
        ends = np.r_[starts[1:], len(order)]
        for s, e in zip(starts, ends, strict=True):
            if e - s < 2:
                continue
            members = np.sort(order[s:e])
            first = int(members[0])
            for other in members[1:]:
                pairs.add((first, int(other)))
    return pairs


class UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, x: int, y: int) -> None:
        rx, ry = self.find(x), self.find(y)
        if rx != ry:
            self.parent[max(rx, ry)] = min(rx, ry)


def near_duplicate_groups(
    signatures: np.ndarray, layout: BandLayout, threshold: float
) -> tuple[dict[int, list[int]], dict[tuple[int, int], float]]:
    """Groups (root -> members, ascending) of rows whose verified estimate is >= threshold."""
    verified: dict[tuple[int, int], float] = {}
    uf = UnionFind(signatures.shape[0])
    for i, j in sorted(candidate_pairs(band_keys(signatures, layout))):
        estimate = jaccard_estimate(signatures[i], signatures[j])
        if estimate >= threshold:
            verified[(i, j)] = estimate
            uf.union(i, j)
    groups: dict[int, list[int]] = {}
    for i in sorted({x for pair in verified for x in pair}):
        groups.setdefault(uf.find(i), []).append(i)
    return groups, verified


def signatures_for(
    texts: Sequence[str], *, kind: str, size: int, count: int, seed: int
) -> np.ndarray:
    a, b = permutations(count, seed)
    out = np.empty((len(texts), count), dtype=np.uint32)
    for i, text in enumerate(texts):
        out[i] = signature(shingles(text, kind, size), a, b)
    return out
