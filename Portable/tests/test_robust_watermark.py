"""Tests for the resize-resistant robust watermark (variable payload, <=512 B)."""

from __future__ import annotations

import hashlib

import numpy as np
import pytest
from PIL import Image

from rsw.robust_watermark import (
    MAX_PAYLOAD_BYTES,
    WatermarkConfig,
    WatermarkError,
    embed,
    embed_text,
    extract,
)


def _channel(path_in: str, path_out: str, quality: int = 70, longest: int | None = None) -> str:
    """Simulate a social-media pipeline: optional downscale + hard JPEG (4:2:0)."""
    im = Image.open(path_in).convert("RGB")
    if longest is not None:
        w, h = im.size
        m = max(w, h)
        im = im.resize((round(w * longest / m), round(h * longest / m)), Image.LANCZOS)
    im.save(path_out, "JPEG", quality=quality, subsampling=2)
    return path_out


@pytest.fixture
def big_cover(tmp_path) -> str:
    """A larger textured cover (real-photo-like) for heavy-payload survival tests."""
    n = 960
    yy, xx = np.mgrid[0:n, 0:n]
    base = (120 + 40 * np.sin(xx / 23.0) + 28 * np.cos(yy / 19.0)
            + 20 * np.sin((xx + yy) / 11.0) + 12 * np.sin(xx / 3.3) * np.cos(yy / 3.7))
    r = np.random.default_rng(3)
    im = np.clip(base + r.normal(0, 16, (n, n)), 0, 255).astype(np.uint8)
    rgb = np.stack([im, np.clip(im * 0.92 + 12, 0, 255).astype(np.uint8),
                    np.clip(im * 1.05, 0, 255).astype(np.uint8)], axis=-1)
    path = tmp_path / "big_cover.png"
    Image.fromarray(rgb, "RGB").save(path)
    return str(path)


@pytest.fixture
def smooth_cover(tmp_path) -> str:
    """A realistic photo with large *smooth* regions (gradient sky + plain ground).

    Smooth covers are the hard case: the perceptual mask pulls the watermark out
    of flat areas, so this is where a small social-media download is most likely
    to erase a long payload.  Regression guard for the band-400 tuning.
    """
    n = 1200
    yy, xx = np.mgrid[0:n, 0:n].astype(float)
    img = 180 + 40 * (yy / n)                                   # smooth gradient sky
    img[int(n * 0.55):] = 90 + 15 * np.sin(xx[int(n * 0.55):] / 40.0)   # plain ground
    a, b, c, d = int(n * .30), int(n * .55), int(n * .30), int(n * .65)
    img[a:b, c:d] += 25 * np.sin(xx[a:b, c:d] / 4.0) * np.cos(yy[a:b, c:d] / 4.0)
    img = np.clip(img, 0, 255).astype(np.uint8)
    rgb = np.stack([np.clip(img * 1.02, 0, 255), np.clip(img * 0.98, 0, 255),
                    np.clip(img * 0.95 + 10, 0, 255)], axis=-1).astype(np.uint8)
    path = tmp_path / "smooth_cover.png"
    Image.fromarray(rgb, "RGB").save(path)
    return str(path)


def test_short_text_roundtrip_clean(sample_cover, tmp_path):
    out = str(tmp_path / "marked.png")
    res = embed_text(sample_cover, "authentic:mike-2026", out)
    assert res.valid
    assert res.psnr_db > 32
    got = extract(out)
    assert got.valid
    assert got.text == "authentic:mike-2026"


def test_500_byte_text_roundtrip_clean(sample_cover, tmp_path):
    out = str(tmp_path / "marked.png")
    msg = ("La ciberseguridad protege la verdad. " * 20)[:500]
    assert len(msg.encode("utf-8")) == 500
    embed_text(sample_cover, msg, out)
    got = extract(out)
    assert got.valid
    assert got.text == msg


def test_binary_payload_exact(sample_cover, tmp_path):
    out = str(tmp_path / "marked.png")
    token = hashlib.sha256(b"image-provenance").digest()  # 32 bytes
    embed(sample_cover, token, out)
    got = extract(out)
    assert got.valid
    assert got.payload == token


def test_incompressible_payload_over_max_rejected(sample_cover, tmp_path):
    """The cap is on the *compressed* size, so the guard triggers on
    incompressible (random) bytes that stay above the limit."""
    import os
    out = str(tmp_path / "marked.png")
    with pytest.raises(WatermarkError):
        embed(sample_cover, os.urandom(MAX_PAYLOAD_BYTES + 128), out)


def test_text_over_raw_max_rejected(sample_cover, tmp_path):
    from rsw.robust_watermark import MAX_TEXT_BYTES
    out = str(tmp_path / "marked.png")
    with pytest.raises(WatermarkError):
        embed_text(sample_cover, "x" * (MAX_TEXT_BYTES + 1), out)


def test_compressible_text_over_512_raw_roundtrips(sample_cover, tmp_path):
    """Brotli compression lets ordinary prose exceed 512 *raw* bytes as long as
    it compresses under the cap."""
    out = str(tmp_path / "marked.png")
    msg = ("La ciberseguridad protege la verdad de las imagenes digitales. " * 12)
    assert len(msg.encode("utf-8")) > MAX_PAYLOAD_BYTES
    embed_text(sample_cover, msg, out)
    got = extract(out)
    assert got.valid
    assert got.text == msg


def test_legacy_uncompressed_header_parses(sample_cover):
    """A pre-compression header (8th byte was a 0x00 pad) must parse as
    flags=0 / uncompressed, so images marked by an older build still verify."""
    import struct
    import numpy as np
    from rsw.robust_watermark import _MAGIC, _crc16, _parse_header

    body = struct.pack(">HHB", _MAGIC, 40, 32)
    body += struct.pack(">H", _crc16(body)) + b"\x00"
    bits = np.unpackbits(np.frombuffer(body, dtype=np.uint8))
    valid, length, nsym, flags = _parse_header(bits)
    assert valid and length == 40 and nsym == 32 and flags == 0


def test_unmarked_image_reports_invalid(sample_cover):
    got = extract(sample_cover)
    assert not got.valid


def test_empty_payload_roundtrip(sample_cover, tmp_path):
    out = str(tmp_path / "marked.png")
    embed(sample_cover, b"", out)
    got = extract(out)
    assert got.valid
    assert got.payload == b""


def test_short_text_survives_resize_and_jpeg(big_cover, tmp_path):
    out = str(tmp_path / "marked.png")
    embed_text(big_cover, "hola-mundo-2026", out)
    degraded = _channel(out, str(tmp_path / "m.jpg"), quality=62, longest=800)
    got = extract(degraded)
    assert got.valid
    assert got.text == "hola-mundo-2026"


def test_500_byte_survives_resize_and_jpeg(big_cover, tmp_path):
    """500 bytes through a Facebook/WhatsApp-like resize + hard JPEG."""
    out = str(tmp_path / "marked.png")
    msg = ("provenance|" * 60)[:500]
    embed_text(big_cover, msg, out)
    degraded = _channel(out, str(tmp_path / "m.jpg"), quality=68, longest=1024)
    got = extract(degraded)
    assert got.valid
    assert got.text == msg


def test_survives_pinterest(big_cover, tmp_path):
    """Pinterest resizes pins to a fixed WIDTH (e.g. 736 px) and recompresses."""
    out = str(tmp_path / "marked.png")
    embed_text(big_cover, "autentico:mike-2026", out)
    im = Image.open(out).convert("RGB")
    w, h = im.size
    im = im.resize((736, round(h * 736 / w)), Image.LANCZOS)          # pin width
    pin = str(tmp_path / "pin.jpg")
    im.save(pin, "JPEG", quality=85, subsampling=2)
    got = extract(pin)
    assert got.valid
    assert got.text == "autentico:mike-2026"


def test_survives_small_facebook_download(big_cover, tmp_path):
    """A small Facebook feed download (~480 px) must still recover."""
    out = str(tmp_path / "marked.png")
    embed_text(big_cover, "provenance-token", out)
    degraded = _channel(out, str(tmp_path / "m.jpg"), quality=78, longest=480)
    got = extract(degraded)
    assert got.valid
    assert got.text == "provenance-token"


def test_high_resolution_original_is_capped_and_survives(tmp_path):
    """Regression: a high-resolution original (the reported failure case) is
    downscaled to the 2048 working cap and still survives a small download."""
    from rsw.robust_watermark import _MAX_WORK_SIZE

    n = 3200
    yy, xx = np.mgrid[0:n, 0:n].astype(float)
    base = 150 + 40 * (yy / n) + 25 * np.sin(xx / 40.0)
    r = np.random.default_rng(11)
    im = np.clip(base + r.normal(0, 6, (n, n)), 0, 255).astype(np.uint8)
    rgb = np.stack([im, np.clip(im * 0.96 + 8, 0, 255).astype(np.uint8),
                    np.clip(im * 0.9 + 16, 0, 255).astype(np.uint8)], axis=-1)
    cover = str(tmp_path / "huge.png")
    Image.fromarray(rgb, "RGB").save(cover)

    out = str(tmp_path / "marked.png")
    embed_text(cover, "provenance:2026-highres", out)
    assert max(Image.open(out).size) == _MAX_WORK_SIZE      # capped
    degraded = _channel(out, str(tmp_path / "m.jpg"), quality=78, longest=540)
    got = extract(degraded)
    assert got.valid
    assert got.text == "provenance:2026-highres"


def test_compressed_long_text_survives_channel(big_cover, tmp_path):
    """A long, compressible message (>512 raw) survives a resize + JPEG channel."""
    out = str(tmp_path / "marked.png")
    msg = ("La marca de agua certifica la autenticidad de esta imagen. " * 12)
    assert len(msg.encode("utf-8")) > MAX_PAYLOAD_BYTES     # only fits because it compresses
    embed_text(big_cover, msg, out)
    degraded = _channel(out, str(tmp_path / "m.jpg"), quality=72, longest=1024)
    got = extract(degraded)
    assert got.valid
    assert got.text == msg


def test_large_text_survives_small_download_on_smooth_photo(smooth_cover, tmp_path):
    """Regression: a ~250 B message on a smooth photo (sky/plain ground) must
    survive a small Facebook download.  This is the exact case that failed until
    the per-payload adaptive strength was added."""
    out = str(tmp_path / "marked.png")
    msg = ("**Lorem Ipsum** is simply dummy text of the printing and typesetting "
           "industry. Lorem Ipsum has been the industry's standard dummy text ever "
           "since 1966, when designers at Letraset and James Mosley, the librarian "
           "at St Bride Printing Library in London")[:250]
    embed_text(smooth_cover, msg, out)
    degraded = _channel(out, str(tmp_path / "m.jpg"), quality=78, longest=480)
    got = extract(degraded)
    assert got.valid
    assert got.text == msg


def test_typical_text_survives_webp_download_on_smooth_photo(smooth_cover, tmp_path):
    """A mid-length message on a smooth photo must survive a WebP re-encode
    (Facebook's display format) at a small size."""
    out = str(tmp_path / "marked.png")
    msg = "Foto verificada. ID:2026-07-05. Prohibida su alteracion sin permiso."
    embed_text(smooth_cover, msg, out)
    im = Image.open(out).convert("RGB")
    w, h = im.size
    im = im.resize((540, round(h * 540 / w)), Image.LANCZOS)
    webp = str(tmp_path / "m.webp")
    im.save(webp, "WEBP", quality=80)
    got = extract(webp)
    assert got.valid
    assert got.text == msg


def test_strong_mode_roundtrip(sample_cover, tmp_path):
    out = str(tmp_path / "marked.png")
    embed_text(sample_cover, "x", out, WatermarkConfig.strong())
    got = extract(out)
    assert got.valid
    assert got.text == "x"


def test_legacy_band_still_verifies(sample_cover, tmp_path):
    """An image marked with a previous band must still verify (backward compat)."""
    from rsw.robust_watermark import _LEGACY_BAND_HI

    out = str(tmp_path / "legacy.png")
    legacy_cfg = WatermarkConfig(band_hi=_LEGACY_BAND_HI[0], strength=50.0)
    embed_text(sample_cover, "marca-antigua", out, legacy_cfg)
    got = extract(out)                       # default (current) config
    assert got.valid
    assert got.text == "marca-antigua"


@pytest.mark.parametrize("angle", [90, 180, 270])
def test_survives_hard_rotation(big_cover, tmp_path, angle):
    """A 90/180/270-degree rotation (baked-in EXIF) is recovered by resync."""
    out = str(tmp_path / "marked.png")
    embed_text(big_cover, "orientacion-2026", out)
    rotated = Image.open(out).convert("RGB").rotate(angle, expand=True)
    rpath = str(tmp_path / "rot.png")
    rotated.save(rpath)
    got = extract(rpath)
    assert got.valid
    assert got.text == "orientacion-2026"



def test_flipped_image_no_false_positive(sample_cover, tmp_path):
    plain = str(tmp_path / "plain.png")
    Image.open(sample_cover).convert("RGB").transpose(Image.FLIP_LEFT_RIGHT).save(plain)
    assert not extract(plain).valid
