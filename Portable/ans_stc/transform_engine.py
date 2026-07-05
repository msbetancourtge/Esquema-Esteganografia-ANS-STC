"""Image <-> DCT transform layer.

The carrier image is split into the JPEG 8x8 block grid and transformed with an
orthonormal type-II DCT.  Only *mid-frequency* coefficients are exposed to the
embedder: the DC term is too visible and the highest frequencies are the first
casualties of JPEG compression, so the mid band is the robustness sweet spot.

Embedding happens on the parities of the **quantized** coefficients.  A generous
quantization step (see :data:`ans_stc.config.QUANT_STEP`) gives each parity bit
enough headroom to survive the spatial<->frequency rounding of a lossless
(PNG) round-trip; anything left over is mopped up by the Reed-Solomon layer.
"""

from __future__ import annotations

import numpy as np
from PIL import Image
from scipy.fft import dctn, idctn

from .config import StegoConfig

_CHANNEL_INDEX = {"R": 0, "G": 1, "B": 2}


# --------------------------------------------------------------------------- #
# Zig-zag scan (shared, deterministic order)
# --------------------------------------------------------------------------- #


def zigzag_positions(n: int = 8) -> list[tuple[int, int]]:
    """Return the JPEG zig-zag scan as a list of ``(row, col)`` pairs."""
    order: list[tuple[int, int]] = []
    for s in range(2 * n - 1):
        if s % 2 == 0:
            r = min(s, n - 1)
            c = s - r
            while r >= 0 and c < n:
                order.append((r, c))
                r -= 1
                c += 1
        else:
            c = min(s, n - 1)
            r = s - c
            while c >= 0 and r < n:
                order.append((r, c))
                r += 1
                c -= 1
    return order


ZIGZAG = zigzag_positions(8)


# --------------------------------------------------------------------------- #
# DCT carrier
# --------------------------------------------------------------------------- #


class DctCarrier:
    """Hold the block-DCT of one image plane and mediate coefficient access."""

    def __init__(self, plane: np.ndarray, config: StegoConfig):
        self.config = config
        b = config.block
        self.plane = plane.astype(np.float64)
        self.H, self.W = self.plane.shape
        self.Hc = (self.H // b) * b
        self.Wc = (self.W // b) * b
        if self.Hc == 0 or self.Wc == 0:
            raise ValueError("image is smaller than one DCT block")
        self.nby = self.Hc // b
        self.nbx = self.Wc // b

        grid = self.plane[: self.Hc, : self.Wc]
        self.dct = self._forward(grid)

        sel = [ZIGZAG[k] for k in config.mid_freq_zigzag]
        self._rows = np.array([r for r, _ in sel])
        self._cols = np.array([c for _, c in sel])
        self.k = len(sel)
        self.n_coeffs = self.nby * self.nbx * self.k

    # -- transforms -------------------------------------------------------- #
    def _forward(self, grid: np.ndarray) -> np.ndarray:
        b = self.config.block
        blocks = grid.reshape(self.nby, b, self.nbx, b).transpose(0, 2, 1, 3)
        return dctn(blocks, type=2, norm="ortho", axes=(-2, -1))

    def to_plane(self) -> np.ndarray:
        b = self.config.block
        blocks = idctn(self.dct, type=2, norm="ortho", axes=(-2, -1))
        grid = blocks.transpose(0, 2, 1, 3).reshape(self.Hc, self.Wc)
        out = self.plane.copy()
        out[: self.Hc, : self.Wc] = grid
        return out

    # -- coefficient access ------------------------------------------------ #
    def selected(self) -> np.ndarray:
        """Raw (unquantized) mid-frequency coefficients, deterministic order."""
        vals = self.dct[:, :, self._rows, self._cols]  # (nby, nbx, k)
        return vals.reshape(-1)

    def quantized(self) -> np.ndarray:
        return np.round(self.selected() / self.config.quant_step).astype(np.int64)

    def cover_parities(self) -> np.ndarray:
        return (self.quantized() & 1).astype(np.uint8)

    def set_quantized(self, qvals: np.ndarray) -> None:
        q = self.config.quant_step
        vals = (qvals.astype(np.float64) * q).reshape(self.nby, self.nbx, self.k)
        self.dct[:, :, self._rows, self._cols] = vals

    def embed_parities(self, target_parity: np.ndarray) -> int:
        """Force each selected coefficient's quantized LSB to ``target_parity``.

        Coefficients are nudged by +/-1 quantization level in whichever direction
        stays closer to the original value, minimising the added distortion.
        Returns the number of coefficients actually modified.
        """
        target_parity = np.asarray(target_parity, dtype=np.int64).ravel()
        qstep = self.config.quant_step
        raw = self.selected()
        qvals = np.round(raw / qstep).astype(np.int64)
        need = (qvals & 1) != target_parity
        frac = raw / qstep - qvals            # in [-0.5, 0.5]
        step = np.where(frac >= 0, 1, -1)
        new_q = qvals.copy()
        new_q[need] += step[need]
        self.set_quantized(new_q)
        return int(need.sum())


# --------------------------------------------------------------------------- #
# Colour-plane extraction and re-assembly
# --------------------------------------------------------------------------- #


class ImageCarrier:
    """Load an image, expose one colour plane for embedding, rebuild and save."""

    def __init__(self, array: np.ndarray, mode: str, channel: str):
        self.array = array
        self.mode = mode
        self.channel = channel

    @classmethod
    def load(cls, path: str, channel: str = "B") -> "ImageCarrier":
        img = Image.open(path)
        if img.mode in ("L", "I;16", "I"):
            arr = np.asarray(img.convert("L"))
            return cls(arr.copy(), "L", "L")
        arr = np.asarray(img.convert("RGB"))
        return cls(arr.copy(), "RGB", channel)

    def plane(self) -> np.ndarray:
        if self.mode == "L":
            return self.array.astype(np.float64)
        return self.array[:, :, _CHANNEL_INDEX[self.channel]].astype(np.float64)

    def with_plane(self, new_plane: np.ndarray) -> np.ndarray:
        clipped = np.clip(np.round(new_plane), 0, 255).astype(np.uint8)
        if self.mode == "L":
            return clipped
        out = self.array.copy()
        out[:, :, _CHANNEL_INDEX[self.channel]] = clipped
        return out

    def save(self, path: str, array: np.ndarray) -> None:
        mode = "L" if self.mode == "L" else "RGB"
        Image.fromarray(array, mode=mode).save(path)

    @property
    def size(self) -> tuple[int, int]:
        return self.array.shape[1], self.array.shape[0]
