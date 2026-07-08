"""J-UNIWARD embedding-distortion costs.

J-UNIWARD (*JPEG Universal Wavelet Relative Distortion*, Holub, Fridrich &
Denemark, 2014) measures the cost of modifying a DCT coefficient by how much the
change disturbs an undecimated Daubechies-8 wavelet decomposition of the spatial
image, relative to the image's own wavelet content::

    rho(b, i, j) = sum_k sum_{u,v} |dR_k(u,v)| / (sigma + |R_k(u,v)|)

where ``R_k`` are the three directional wavelet residuals of the cover and
``dR_k`` is the residual change induced by a unit modification of DCT mode
``(i, j)`` in block ``b``.  Smooth regions have small ``|R_k|`` -> huge cost
(avoid); textured regions have large ``|R_k|`` -> small cost (prefer).  This is
what steers the STC embedder into content that best masks the changes.
"""

from __future__ import annotations

import numpy as np
from scipy.fft import idctn
from scipy.signal import fftconvolve

from .config import StegoConfig
from .transform_engine import ZIGZAG

# Daubechies-8 decomposition low-pass filter (16 taps, sum == sqrt(2)).
_DB8_DEC_LO = np.array(
    [
        -0.00011747678400228192,
        0.0006754494059985568,
        -0.0003917403729959771,
        -0.00487035299301066,
        0.008746094047015655,
        0.013981027917015516,
        -0.04408825393106472,
        -0.01736930100202211,
        0.128747426620186,
        0.00047248457399797254,
        -0.2840155429624281,
        -0.015829105256023893,
        0.5853546836548691,
        0.6756307362980128,
        0.3128715909144659,
        0.05441584224308161,
    ]
)

# Stabilising constant from the reference implementation.
SIGMA = 2.0**-6
WET_COST = 1.0e10  # cap so the STC Viterbi stays numerically well behaved


def _db8_filters() -> tuple[np.ndarray, np.ndarray]:
    dec_lo = _DB8_DEC_LO
    n = len(dec_lo)
    # Quadrature-mirror high-pass: g[k] = (-1)^k * h[N-1-k].
    dec_hi = dec_lo[::-1].copy()
    dec_hi[1::2] *= -1
    return dec_lo, dec_hi


def _directional_kernels() -> list[np.ndarray]:
    lo, hi = _db8_filters()
    # LH, HL, HH directional 2-D wavelet kernels.
    return [np.outer(lo, hi), np.outer(hi, lo), np.outer(hi, hi)]


def _basis_image(i: int, j: int, block: int = 8) -> np.ndarray:
    """Orthonormal inverse-DCT basis image of mode ``(i, j)``."""
    impulse = np.zeros((block, block))
    impulse[i, j] = 1.0
    return idctn(impulse, type=2, norm="ortho")


def juniward_costs(
    plane: np.ndarray, config: StegoConfig, sigma: float = SIGMA
) -> np.ndarray:
    """Return per-coefficient costs aligned with :meth:`DctCarrier.selected`.

    The output order is block row-major then selected-mode order, i.e. flat index
    ``(by * nbx + bx) * K + k`` — identical to the coefficient vector produced by
    the transform engine, so costs and cover parities line up one-to-one.
    """
    block = config.block
    Hc = (plane.shape[0] // block) * block
    Wc = (plane.shape[1] // block) * block
    grid = plane[:Hc, :Wc].astype(np.float64)

    kernels = _directional_kernels()

    # Directional residual reciprocals xi_k = 1 / (sigma + |R_k|).
    xis = [1.0 / (sigma + np.abs(fftconvolve(grid, ker, mode="same"))) for ker in kernels]

    sel = [ZIGZAG[k] for k in config.mid_freq_zigzag]
    nby, nbx = Hc // block, Wc // block
    K = len(sel)
    costs = np.zeros((nby, nbx, K), dtype=np.float64)

    anchor_r = np.arange(nby) * block + block // 2
    anchor_c = np.arange(nbx) * block + block // 2

    for kk, (i, j) in enumerate(sel):
        footprint = _basis_image(i, j, block) * config.quant_step
        acc = np.zeros_like(grid)
        for ker, xi in zip(kernels, xis):
            impact = fftconvolve(footprint, ker, mode="full")
            acc += fftconvolve(xi, np.abs(impact)[::-1, ::-1], mode="same")
        costs[:, :, kk] = acc[np.ix_(anchor_r, anchor_c)]

    np.clip(costs, 1.0e-4, WET_COST, out=costs)
    return costs.reshape(-1)
