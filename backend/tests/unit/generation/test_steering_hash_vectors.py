"""miLLM's steering-set hash, pinned byte for byte (007 FTASKS 3.7; X-07).

Vectors from miLLM ``0xcc/tdds/028_FTDD|Inline_Steering_And_Steering_Header.md`` section 5.3 and
``tests/unit/core/test_steering_state.py`` (TV-1 to TV-4, recomputed by miLLM's Stage 3 reviewer).
Each vector pins BOTH the canonical text and the hash: a drift in either breaks every steered pair
recorded so far, so the mutation controls (zeros kept, sort dropped, ``repr`` instead of binary64,
the sent instead of the applied set) must each turn a named vector red.
"""

from __future__ import annotations

import pytest

from src.services.generation import steering

LFM = "LiquidAI--LFM2.5-1.2B-Instruct-sae--layer_11"
GEMMA = "jbloom--gemma-2-2b-res-jb--layer_20--width_16k--average_l0_71"

VECTORS = [
    (
        "TV-1",
        LFM,
        [(1234, 8.0)],
        "sha256:a4eae730e5105f422b93abeece7e03bda3c29b096c07aa9cee6f247e12844105",
    ),
    (
        "TV-2",
        LFM,
        [(1234, -8.0)],
        "sha256:cc6c48faa720096e5c65fc1b2afab1b9b87659fb4465f20db421a2ed7fed78a7",
    ),
    (
        "TV-3",
        GEMMA,
        [(77, -2.5), (5, 0.1), (900, 0.0), (12, -0.0)],
        "sha256:b843912201c5c18f872976e35af288e5f484d81e31a03a1dc16c3b9dbd82e710",
    ),
    (
        "TV-4",
        GEMMA,
        [(3, 500.0)],
        "sha256:3f51779e6e33ee248acf6f520cb9475b4ef62f68275060bb69d2e228033b78df",
    ),
]


@pytest.mark.parametrize(("name", "sae", "sent", "digest"), VECTORS, ids=[v[0] for v in VECTORS])
def test_the_applied_set_hash_reproduces_every_published_vector(
    name: str, sae: str, sent: list[tuple[int, float]], digest: str
) -> None:
    assert steering.applied_set_hash(sae, sent) == digest, name


def test_tv1_canonical_text_byte_for_byte() -> None:
    text = steering.canonical_steering_form(LFM, [(1234, 8.0)])
    assert text == (
        b"millm.steering-set/v1\n"
        b"sae=LiquidAI--LFM2.5-1.2B-Instruct-sae--layer_11\n"
        b"1234:4020000000000000\n"
    )


def test_tv3_drops_both_zeros_and_sorts_by_index() -> None:
    text = steering.canonical_steering_form(GEMMA, steering.applied_pairs(VECTORS[2][2]).items())
    assert text == (
        b"millm.steering-set/v1\n"
        b"sae=jbloom--gemma-2-2b-res-jb--layer_20--width_16k--average_l0_71\n"
        b"5:3fb999999999999a\n"
        b"77:c004000000000000\n"
    )


def test_tv4_hashes_the_clamped_value_not_the_sent_one() -> None:
    sent_hash = steering.steering_set_hash(GEMMA, [(3, 500.0)])
    applied = steering.applied_set_hash(GEMMA, [(3, 500.0)])
    assert applied == VECTORS[3][3]
    assert sent_hash != applied
    assert steering.clamped_count([(3, 500.0)]) == 1


def test_tv1_and_tv2_differ_on_sign_alone() -> None:
    assert VECTORS[0][3] != VECTORS[1][3]


def test_an_sae_id_with_a_line_break_is_refused() -> None:
    with pytest.raises(ValueError, match="line break"):
        steering.canonical_steering_form("a\nb", [(1, 1.0)])


def test_the_canonical_form_itself_drops_zeros_without_the_clamp_helper() -> None:
    """Control C6: a caller hashing a raw set (zeros included) still gets TV-3."""
    assert steering.steering_set_hash(GEMMA, VECTORS[2][2]) == VECTORS[2][3]
