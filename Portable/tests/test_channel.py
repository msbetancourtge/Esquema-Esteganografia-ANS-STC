"""Robustness tests: survival across the JPEG channel."""

from __future__ import annotations

import numpy as np

from rsw.channel_simulator import awgn_channel, evaluate, jpeg_channel
from rsw.config import StegoConfig
from rsw.payload_manager import pack_text


def test_robust_preset_survives_social_media_jpeg(sample_cover) -> None:
    container = pack_text("Robust payload that must survive JPEG. " * 4)
    rows = {r.quality: r for r in evaluate(sample_cover, container, StegoConfig.robust(),
                                           qualities=(80, 90))}
    assert rows[90].recovered
    assert rows[80].recovered


def test_avalanche_amplification_is_visible(sample_cover) -> None:
    # STC turns a few coefficient flips into many message-bit flips; that burst
    # is exactly what Reed-Solomon is there to absorb.
    container = pack_text("avalanche probe " * 8)
    rows = evaluate(sample_cover, container, StegoConfig.max_quality(), qualities=(85,))
    row = rows[0]
    if row.coeff_ber > 0:
        assert row.msg_ber > row.coeff_ber


def test_jpeg_channel_returns_same_shape() -> None:
    arr = np.random.default_rng(0).integers(0, 256, (64, 64, 3)).astype(np.uint8)
    out = jpeg_channel(arr, quality=80)
    assert out.shape == arr.shape


def test_awgn_channel_bounds() -> None:
    arr = np.full((32, 32, 3), 128, dtype=np.uint8)
    out = awgn_channel(arr, sigma=5.0)
    assert out.dtype == np.uint8
    assert out.min() >= 0 and out.max() <= 255
