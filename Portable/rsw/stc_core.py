"""Syndrome-Trellis Codes (STC): minimum-distortion syndrome coding.

STC embeds a message ``m`` into a cover vector ``x`` (here the parities of the
selected DCT coefficients) by producing a stego vector ``y`` that satisfies the
linear constraint ``H @ y = m (mod 2)`` while minimising the total embedding
distortion ``sum(rho_i * [x_i != y_i])``.

* **Embedding** is the Viterbi search over the syndrome trellis defined by a
  small shared sub-matrix :math:`\\hat H` (height ``h`` = constraint length,
  width ``w``).  The parity-check matrix ``H`` is :math:`\\hat H` tiled along the
  diagonal, one column-block (``w`` cover elements) per message bit.
* **Extraction** is the cheap linear map ``m = H @ y (mod 2)``.

Because extraction only depends on the syndrome, correctness is guaranteed as
long as embedding returns a ``y`` with ``H @ y == m`` — a property this module
asserts at run time and the test-suite checks by brute force.
"""

from __future__ import annotations

import numpy as np

from .config import STC_HEIGHT

_SUBMATRIX_SEED = 0x5715C0DE


class STCError(Exception):
    """Raised when a message cannot be embedded in the available cover."""


# --------------------------------------------------------------------------- #
# Parity-check construction
# --------------------------------------------------------------------------- #


def build_submatrix(h: int, w: int) -> np.ndarray:
    """Return the deterministic ``h x w`` STC sub-matrix :math:`\\hat H`.

    The first and last rows are forced to all-ones which keeps the trellis fully
    connected (every column influences the current syndrome bit) so that *any*
    message is embeddable.  The interior is pseudo-random but seeded, therefore
    identical on the embed and extract sides.
    """
    if h < 2 or w < 1:
        raise ValueError("STC needs h >= 2 and w >= 1")
    rng = np.random.default_rng(_SUBMATRIX_SEED + h * 131 + w)
    hhat = rng.integers(0, 2, size=(h, w), dtype=np.uint8)
    hhat[0, :] = 1
    hhat[h - 1, :] = 1
    return hhat


def _column_vectors(hhat: np.ndarray) -> np.ndarray:
    """Pack each column of :math:`\\hat H` into an integer (bit r -> row r)."""
    h, w = hhat.shape
    weights = (1 << np.arange(h, dtype=np.int64))
    return (hhat.astype(np.int64) * weights[:, None]).sum(axis=0)


def build_parity_matrix(
    m: int, h: int, w: int, hhat: np.ndarray | None = None
) -> np.ndarray:
    """Dense ``m x (m*w)`` parity-check matrix (used by tests / extraction)."""
    if hhat is None:
        hhat = build_submatrix(h, w)
    n = m * w
    H = np.zeros((m, n), dtype=np.uint8)
    rows_by_col = [np.nonzero(hhat[:, j])[0] for j in range(w)]
    for i in range(m):
        for j in range(w):
            c = i * w + j
            rows = i + rows_by_col[j]
            rows = rows[rows < m]
            H[rows, c] = 1
    return H


def capacity(n: int, w: int) -> int:
    """Number of message bits embeddable in ``n`` cover elements at width ``w``."""
    return n // w


# --------------------------------------------------------------------------- #
# Extraction  (m = H y mod 2)
# --------------------------------------------------------------------------- #


def stc_extract(
    stego: np.ndarray, num_bits: int, h: int, w: int, hhat: np.ndarray | None = None
) -> np.ndarray:
    """Recover ``num_bits`` message bits from the stego parity vector."""
    if hhat is None:
        hhat = build_submatrix(h, w)
    y = np.asarray(stego, dtype=np.uint8).ravel()[: num_bits * w]
    syndrome = np.zeros(num_bits, dtype=np.int64)
    ones = np.nonzero(y)[0]
    if ones.size:
        blk = ones // w
        col = ones % w
        for r in range(h):
            sel = (hhat[r, col] == 1) & (blk + r < num_bits)
            if np.any(sel):
                np.add.at(syndrome, blk[sel] + r, 1)
    return (syndrome & 1).astype(np.uint8)


# --------------------------------------------------------------------------- #
# Embedding  (Viterbi over the syndrome trellis)
# --------------------------------------------------------------------------- #


def stc_embed(
    cover: np.ndarray,
    costs: np.ndarray,
    message: np.ndarray,
    h: int = STC_HEIGHT,
    w: int = 2,
    hhat: np.ndarray | None = None,
    verify: bool = True,
) -> np.ndarray:
    """Return a stego parity vector ``y`` with ``H @ y == message``.

    Parameters
    ----------
    cover, costs
        Equal-length arrays: the cover parities and the per-element distortion
        of flipping each parity.
    message
        The bits to embed; ``len(message) * w`` must not exceed ``len(cover)``.
    h, w
        STC constraint height and width.
    verify
        When ``True`` (default) the syndrome of the result is checked against the
        message and :class:`STCError` is raised on any mismatch.
    """
    cover = np.asarray(cover, dtype=np.uint8).ravel()
    costs = np.asarray(costs, dtype=np.float64).ravel()
    message = np.asarray(message, dtype=np.uint8).ravel()
    m = int(message.size)
    n = m * w
    if n > cover.size:
        raise STCError(
            f"message needs {n} cover elements but only {cover.size} are available"
        )
    if costs.size < n:
        raise STCError("costs array is shorter than the cover region")
    if hhat is None:
        hhat = build_submatrix(h, w)
    colvec = _column_vectors(hhat)

    n_states = 1 << h
    inf = np.float64(1e18)
    wght = np.full(n_states, inf)
    wght[0] = 0.0
    idx = np.arange(n_states, dtype=np.int64)

    x = cover[:n]
    rho = costs[:n]
    path = np.zeros((n, n_states), dtype=np.uint8)

    half = n_states >> 1
    keep = np.arange(half, dtype=np.int64) << 1

    indx = 0
    for i in range(m):
        rows_avail = h if (m - i) >= h else (m - i)
        rowmask = (1 << rows_avail) - 1
        for j in range(w):
            col = int(colvec[j]) & rowmask
            xi = x[indx]
            ri = rho[indx]
            c0 = ri if xi else 0.0          # cost to force parity 0
            c1 = 0.0 if xi else ri          # cost to force parity 1
            w0 = wght + c0
            w1 = wght[idx ^ col] + c1
            choose1 = w1 < w0
            wght = np.where(choose1, w1, w0)
            path[indx] = choose1.astype(np.uint8)
            indx += 1
        # Enforce message bit i on the settled syndrome bit, then shift the window.
        mb = int(message[i])
        new_wght = np.full(n_states, inf)
        new_wght[:half] = wght[keep | mb]
        wght = new_wght

    if not np.isfinite(wght[0]):
        raise STCError("no valid stego vector found (degenerate sub-matrix)")

    # Back-trace from the fully-satisfied terminal state 0.
    y = cover.copy()
    state = 0
    indx = n
    for i in range(m - 1, -1, -1):
        rows_avail = h if (m - i) >= h else (m - i)
        rowmask = (1 << rows_avail) - 1
        state = (state << 1) | int(message[i])
        for j in range(w - 1, -1, -1):
            indx -= 1
            col = int(colvec[j]) & rowmask
            bit = int(path[indx, state])
            y[indx] = bit
            if bit:
                state ^= col

    if verify:
        recovered = stc_extract(y, m, h, w, hhat)
        if not np.array_equal(recovered, message):
            raise STCError("internal STC failure: syndrome mismatch after embedding")
    return y
