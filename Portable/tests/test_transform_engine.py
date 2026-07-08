"""Tests for the DCT transform engine and cost calculator."""

from __future__ import annotations

import numpy as np
import pytest

from rsw.config import StegoConfig
from rsw.cost_calculator import _db8_filters, juniward_costs
from rsw.transform_engine import ZIGZAG, DctCarrier, ImageCarrier, zigzag_positions


def test_zigzag_is_a_permutation() -> None:
    z = zigzag_positions(8)
    assert len(z) == 64
    assert z[0] == (0, 0)
    assert len(set(z)) == 64


def test_dct_roundtrip_is_near_lossless(rng) -> None:
    plane = rng.uniform(0, 255, (64, 64))
    cfg = StegoConfig()
    carrier = DctCarrier(plane, cfg)
    recon = carrier.to_plane()
    assert np.allclose(recon, plane, atol=1e-6)


def test_selected_length_matches_n_coeffs(rng) -> None:
    plane = rng.uniform(0, 255, (128, 96))
    cfg = StegoConfig()
    carrier = DctCarrier(plane, cfg)
    assert carrier.selected().shape[0] == carrier.n_coeffs
    assert carrier.cover_parities().shape[0] == carrier.n_coeffs


def test_parity_roundtrip_through_png(tmp_path, rng) -> None:
    from PIL import Image

    cfg = StegoConfig()
    yy, xx = np.mgrid[0:128, 0:128]
    plane = np.clip(128 + 40 * np.sin(xx / 9.0) + rng.normal(0, 8, (128, 128)), 0, 255)
    rgb = np.stack([plane, plane, plane], -1).astype(np.uint8)
    p = tmp_path / "img.png"
    Image.fromarray(rgb, "RGB").save(p)

    ic = ImageCarrier.load(str(p), cfg.channel)
    carrier = DctCarrier(ic.plane(), cfg)
    target = rng.integers(0, 2, carrier.n_coeffs).astype(np.uint8)
    carrier.embed_parities(target)
    stego = ic.with_plane(carrier.to_plane())
    sp = tmp_path / "stego.png"
    ic.save(str(sp), stego)

    ic2 = ImageCarrier.load(str(sp), cfg.channel)
    recovered = DctCarrier(ic2.plane(), cfg).cover_parities()
    assert np.array_equal(recovered, target)  # lossless round-trip


def test_db8_filter_properties() -> None:
    lo, hi = _db8_filters()
    assert lo.sum() == pytest.approx(np.sqrt(2), abs=1e-9)
    assert hi.sum() == pytest.approx(0.0, abs=1e-9)


def test_juniward_is_adaptive() -> None:
    rng = np.random.default_rng(3)
    cfg = StegoConfig()
    plane = np.full((128, 256), 128.0)
    plane[:, 128:] += rng.normal(0, 30, (128, 128))
    plane = np.clip(plane, 0, 255)

    costs = juniward_costs(plane, cfg)
    carrier = DctCarrier(plane, cfg)
    assert costs.shape[0] == carrier.n_coeffs
    assert np.all(np.isfinite(costs))

    per_block = costs.reshape(carrier.nby, carrier.nbx, carrier.k).mean(axis=2)
    smooth = per_block[:, : carrier.nbx // 2].mean()
    textured = per_block[:, carrier.nbx // 2 :].mean()
    assert smooth > textured * 3  # smooth regions must cost far more
