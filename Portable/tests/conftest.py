"""Shared pytest fixtures."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(1234)


def _textured_rgb(h: int = 384, w: int = 384, seed: int = 7) -> np.ndarray:
    r = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    base = (
        115
        + 45 * np.sin(xx / 19.0)
        + 30 * np.cos(yy / 15.0)
        + 20 * np.sin((xx + yy) / 9.0)
        + 15 * np.cos((xx - yy) / 6.0)
    )

    def ch(mul: float, off: float) -> np.ndarray:
        return np.clip(base * mul + off + r.normal(0, 10, (h, w)), 0, 255)

    return np.stack([ch(1.0, 0), ch(0.92, 18), ch(1.05, -8)], axis=-1).astype(np.uint8)


@pytest.fixture
def sample_cover(tmp_path) -> str:
    path = tmp_path / "cover.png"
    Image.fromarray(_textured_rgb(), "RGB").save(path)
    return str(path)


@pytest.fixture
def small_secret_image(tmp_path) -> str:
    # A smooth 16x16 gradient (compresses well, keeps the payload small).
    yy, xx = np.mgrid[0:16, 0:16]
    arr = np.stack([(xx * 16), (yy * 16), ((xx + yy) * 8)], axis=-1).astype(np.uint8)
    path = tmp_path / "secret_logo.png"
    Image.fromarray(arr, "RGB").save(path)
    return str(path)
