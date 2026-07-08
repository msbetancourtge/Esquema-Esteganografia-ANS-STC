"""End-to-end pipeline tests (Hito 1 acceptance)."""

from __future__ import annotations

import numpy as np
import pytest

from rsw.config import StegoConfig
from rsw.payload_manager import PayloadError
from rsw.pipeline import (
    CapacityError,
    capacity_bytes,
    extract,
    hide_file,
    hide_text,
)

PRESETS = [StegoConfig.max_quality(), StegoConfig.balanced(), StegoConfig.robust()]


@pytest.mark.parametrize("config", PRESETS, ids=["max", "balanced", "robust"])
def test_text_roundtrip(sample_cover, tmp_path, config) -> None:
    secret = "Anti-deepfake watermark — ANS+STC+RS. Ñandú, café, 42."
    out = str(tmp_path / "stego.png")
    result = hide_text(sample_cover, secret, out, config)
    assert result.coeffs_changed > 0
    assert result.psnr_db > 30
    recovered = extract(out, config)
    assert recovered.payload.as_text() == secret


def test_file_roundtrip(sample_cover, tmp_path) -> None:
    config = StegoConfig.robust()
    blob = bytes(range(256)) * 3
    src = tmp_path / "payload.bin"
    src.write_bytes(blob)
    out = str(tmp_path / "stego.png")
    hide_file(sample_cover, str(src), out, image=False, config=config)
    recovered = extract(out, config)
    assert recovered.payload.kind == "file"
    assert recovered.payload.name == "payload.bin"
    assert recovered.payload.data == blob


def test_image_payload_roundtrip(sample_cover, small_secret_image, tmp_path) -> None:
    config = StegoConfig.robust()
    out = str(tmp_path / "stego.png")
    hide_file(sample_cover, small_secret_image, out, image=True, config=config)
    recovered = extract(out, config)
    assert recovered.payload.kind == "image"
    assert recovered.payload.name.endswith(".png")


def test_capacity_error_on_oversized_payload(sample_cover, tmp_path) -> None:
    config = StegoConfig.robust()
    cap = capacity_bytes(sample_cover, config)
    assert cap > 0
    # Incompressible random data larger than the raw capacity cannot fit.
    big = tmp_path / "big.bin"
    big.write_bytes(np.random.default_rng(0).integers(0, 256, cap * 2).astype("uint8").tobytes())
    with pytest.raises(CapacityError):
        hide_file(sample_cover, str(big), str(tmp_path / "x.png"), config=config)


def test_extracting_plain_image_fails_cleanly(sample_cover) -> None:
    # A cover with no payload must not masquerade as a valid stego image.
    with pytest.raises(PayloadError):
        extract(sample_cover, StegoConfig.robust())


def test_change_rate_is_low(sample_cover, tmp_path) -> None:
    config = StegoConfig.robust()
    out = str(tmp_path / "stego.png")
    result = hide_text(sample_cover, "short secret", out, config)
    # J-UNIWARD + STC should touch only a small fraction of coefficients.
    assert result.change_rate < 0.10
