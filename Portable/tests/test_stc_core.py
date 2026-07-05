"""Tests for the Syndrome-Trellis Codes core."""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from ans_stc import stc_core as stc


def _distortion(x: np.ndarray, y: np.ndarray, rho: np.ndarray) -> float:
    return float(np.sum(rho[: len(y)] * (x[: len(y)] != y)))


def test_submatrix_is_deterministic_and_well_formed() -> None:
    a = stc.build_submatrix(8, 3)
    b = stc.build_submatrix(8, 3)
    assert np.array_equal(a, b)
    assert np.all(a[0, :] == 1)
    assert np.all(a[-1, :] == 1)


@pytest.mark.parametrize("seed", range(25))
def test_bruteforce_optimality(seed: int) -> None:
    rng = np.random.default_rng(seed)
    h = int(rng.integers(2, 5))
    w = int(rng.integers(1, 4))
    m = int(rng.integers(1, 5))
    n = m * w
    hhat = stc.build_submatrix(h, w)
    H = stc.build_parity_matrix(m, h, w, hhat)
    x = rng.integers(0, 2, n).astype(np.uint8)
    rho = rng.random(n) + 0.05
    msg = rng.integers(0, 2, m).astype(np.uint8)

    y = stc.stc_embed(x, rho, msg, h, w, hhat, verify=True)
    assert np.array_equal((H @ y[:n]) % 2, msg)
    assert np.array_equal(stc.stc_extract(y, m, h, w, hhat), msg)

    best = min(
        _distortion(x, np.array(bits, dtype=np.uint8), rho)
        for bits in itertools.product([0, 1], repeat=n)
        if np.array_equal((H @ np.array(bits, dtype=np.uint8)) % 2, msg)
    )
    assert _distortion(x, y, rho) == pytest.approx(best)


@pytest.mark.parametrize("w", [1, 2, 3, 5])
def test_large_scale_correctness(w: int) -> None:
    rng = np.random.default_rng(100 + w)
    h = 8
    n = 8000
    m = stc.capacity(n, w)
    x = rng.integers(0, 2, n).astype(np.uint8)
    rho = rng.random(n) + 0.01
    msg = rng.integers(0, 2, m).astype(np.uint8)
    y = stc.stc_embed(x, rho, msg, h, w, verify=True)
    assert np.array_equal(stc.stc_extract(y, m, h, w), msg)


def test_message_too_large_raises() -> None:
    x = np.zeros(10, dtype=np.uint8)
    rho = np.ones(10)
    msg = np.ones(20, dtype=np.uint8)  # needs 20*w cover elements
    with pytest.raises(stc.STCError):
        stc.stc_embed(x, rho, msg, h=8, w=2)


def test_zero_cost_regions_are_free() -> None:
    # Coefficients with zero cost should be flipped preferentially.
    rng = np.random.default_rng(3)
    h, w, m = 6, 2, 20
    n = m * w
    x = rng.integers(0, 2, n).astype(np.uint8)
    rho = np.ones(n)
    rho[: n // 2] = 0.0
    msg = rng.integers(0, 2, m).astype(np.uint8)
    y = stc.stc_embed(x, rho, msg, h, w, verify=True)
    changes_expensive = int(np.sum(x[n // 2 :] != y[n // 2 :]))
    changes_free = int(np.sum(x[: n // 2] != y[: n // 2]))
    assert changes_free >= changes_expensive
