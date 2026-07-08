"""Resize-resistant blind watermark — survives social-media re-processing.

Where the STC/J-UNIWARD pipeline (:mod:`rsw.pipeline`) hides *kilobytes* but
dies when an image is **resized** (Facebook / Instagram / WhatsApp all downscale
and recompress), this module trades capacity for brute robustness: it embeds a
**variable-length payload of up to 512 bytes** that survives that whole gauntlet.

Why it survives where block-DCT steganography cannot
----------------------------------------------------
1. **Canonical-resolution re-synchronisation.**  Both embedder and extractor
   first stretch the luminance to a fixed ``S x S`` grid, so whatever arbitrary
   size a platform resamples the image to, the extractor snaps it back to the
   same canonical grid and the embedding lattice is never lost.
2. **Improved Spread Spectrum (ISS, Malvar & Florencio 2003).**  Each bit is
   spread over dozens of mid-low-frequency DCT coefficients and the host image's
   own energy is algebraically cancelled, so detection (by correlation sign) is
   host-interference-free — enormous processing gain, near-zero raw BER.
3. **Reed-Solomon error correction.**  A parity envelope repairs the residual
   bit errors that a harsh channel still slips through.
4. **Perceptual masking + delta embedding at native resolution.**  Only the
   watermark perturbation is resampled back onto the original image and it is
   pushed into textured regions, so the picture keeps its size/aspect and stays
   visually clean (~38-44 dB depending on length and content).

Layout — two disjoint coefficient regions on the canonical grid::

    header  (64 bits, fixed, ultra-redundant)  MAGIC | LEN | NSYM | CRC-16 | FLAGS
    payload (variable)                         Reed-Solomon( Brotli( user bytes ) )

The header is decoded first; it tells the extractor how many payload bits to
read, which lets short messages use *fewer* bits (higher quality, more margin)
and long messages fill the capacity.  Text is Brotli-compressed first (entropy
coding + a built-in text dictionary), so the 512-byte cap is on the *compressed*
size — compressible prose can be much longer, and fewer bits means more margin
and higher image quality.  A wrong/absent watermark fails the header CRC (or the
RS decode) and is reported as "no watermark" rather than garbage.

Known limitation: resists **resize + recompression**, not hard **cropping or
rotation** (those move/rotate the canonical grid).
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, replace
from functools import lru_cache

import brotli
import numpy as np
from PIL import Image, ImageOps
from reedsolo import ReedSolomonError, RSCodec
from scipy.fft import dctn, idctn
from scipy.ndimage import uniform_filter

MAX_PAYLOAD_BYTES = 512           # max *embedded* size (after optional compression)
MAX_TEXT_BYTES = 4096             # max raw text accepted (compressible text can be long)
_MAGIC = 0xACE5
_HEADER_BITS = 64                 # MAGIC(16)+LEN(16)+NSYM(8)+CRC(16)+FLAGS(8)
_HEADER_COORDS = 6000             # coefficients reserved for the (tiny) header
_RS_BLOCK = 255

_FLAG_COMPRESSED = 0x01           # payload is Brotli-compressed
_MAX_DECOMPRESSED = 64 * 1024     # decompression-bomb guard (payload <= 512 B in)

# Very large originals are capped to this longest side before embedding: it bounds
# the canonical->native->download resampling ratio (which otherwise erodes the
# watermark band on high-resolution photos) and matches what social platforms keep
# anyway (they downscale to <=2048).  Output aspect ratio is preserved.
_MAX_WORK_SIZE = 2048

# A long payload spreads over *fewer* coefficients per bit, so it needs more
# amplitude to keep the same channel margin.  These calibrate the automatic
# per-payload strength boost (see ``_payload_strength``): at/above _ADAPT_REF_CPB
# coefficients-per-bit no boost is applied (short messages stay quiet / high PSNR);
# below it the amplitude ramps up so a full-length message still survives a small
# social-media download, capped at _ADAPT_MAX_BOOST.
_ADAPT_REF_CPB = 400.0
_ADAPT_MAX_BOOST = 1.60

# Older releases used different bands; extraction also tries these so images marked
# by a previous version still verify (no need to re-mark after a band change).
_LEGACY_BAND_HI = (483, 280)


class WatermarkError(Exception):
    """Raised for an oversized payload or when no valid watermark is found."""


@dataclass(frozen=True)
class WatermarkConfig:
    """Everything that must match between embed and extract."""

    canonical_size: int = 1152
    band_lo: int = 8
    band_hi: int = 400            # balances quality + robustness: wide enough that a
    #                              512-byte payload keeps good PSNR and error margin,
    #                              low enough to survive small social-media downloads
    seed: int = 20260704
    nsym: int = 32                # Reed-Solomon parity bytes per 255-byte block
    strength: float = 100.0       # ISS amplitude (higher = more robust, lower PSNR).
    #                              Tuned so real photos (with large smooth sky/skin
    #                              regions) still survive a small Facebook download.
    perceptual_mask: bool = True
    mask_window: int = 9
    mask_lo: float = 0.72         # floor so smooth covers (sky, documents, logos) keep
    #                              enough watermark to survive small downloads
    mask_hi: float = 3.0

    @classmethod
    def strong(cls) -> "WatermarkConfig":
        """Extra margin for repeated / very harsh re-compression (~3 dB lower PSNR)."""
        return cls(strength=130.0)


@dataclass
class WatermarkResult:
    out_path: str | None
    valid: bool
    payload: bytes
    psnr_db: float

    @property
    def text(self) -> str:
        return self.payload.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# Framing helpers
# --------------------------------------------------------------------------- #


def _crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _pack_header(length: int, nsym: int, flags: int) -> np.ndarray:
    body = struct.pack(">HHB", _MAGIC, length, nsym)          # 5 bytes: MAGIC|LEN|NSYM
    body += struct.pack(">H", _crc16(body))                    # +2 = 7 bytes: CRC-16
    body += struct.pack(">B", flags & 0xFF)                    # +1 = 8 bytes = 64 bits
    return np.unpackbits(np.frombuffer(body, dtype=np.uint8))


def _parse_header(bits: np.ndarray) -> tuple[bool, int, int, int]:
    body = np.packbits(bits.astype(np.uint8)).tobytes()
    magic, length, nsym = struct.unpack(">HHB", body[:5])
    crc = struct.unpack(">H", body[5:7])[0]
    flags = body[7]
    # CRC covers MAGIC|LEN|NSYM exactly as the original format, so pre-compression
    # marks (whose 8th byte was a 0x00 pad) parse as flags=0 -> uncompressed.
    valid = magic == _MAGIC and crc == _crc16(body[:5]) and 0 <= length <= MAX_PAYLOAD_BYTES
    return valid, length, nsym, flags


def _compress(raw: bytes) -> tuple[bytes, int]:
    """Brotli-compress ``raw`` if it helps; else return it unchanged.

    Returns ``(body, flags)`` where ``flags`` carries ``_FLAG_COMPRESSED`` when the
    compressed form is actually smaller (short strings often are not compressible).
    """
    if not raw:
        return raw, 0
    packed = brotli.compress(raw, mode=brotli.MODE_TEXT, quality=11)
    if len(packed) < len(raw):
        return packed, _FLAG_COMPRESSED
    return raw, 0


def _decompress(body: bytes, flags: int) -> bytes | None:
    """Undo :func:`_compress`.  Returns ``None`` on corrupt/oversized data."""
    if not (flags & _FLAG_COMPRESSED):
        return body
    try:
        out = brotli.decompress(body)
    except brotli.error:
        return None
    if len(out) > _MAX_DECOMPRESSED:          # decompression-bomb guard
        return None
    return out



def _rs_encoded_len(raw_len: int, nsym: int) -> int:
    """Length in bytes of reedsolo's output for ``raw_len`` input bytes."""
    if raw_len == 0:
        return 0
    chunk = _RS_BLOCK - nsym
    nchunks = max(1, math.ceil(raw_len / chunk))
    return raw_len + nchunks * nsym


def _payload_strength(base: float, payload_coords: int, nbits: int) -> float:
    """Amplitude for the payload region, boosted for long (bit-dense) messages.

    Detection is sign-based, so the extractor never needs to know this value.  A
    short message spreads over hundreds of coefficients per bit and stays quiet
    (high PSNR); a long message spreads over far fewer, so its amplitude ramps up
    (``~1/sqrt(coeffs-per-bit)``) to hold the same channel margin through a small
    social-media download, capped at ``_ADAPT_MAX_BOOST``.
    """
    if nbits <= 0:
        return base
    cpb = payload_coords / nbits
    if cpb >= _ADAPT_REF_CPB:
        return base
    boost = min(_ADAPT_MAX_BOOST, math.sqrt(_ADAPT_REF_CPB / cpb))
    return base * boost


# --------------------------------------------------------------------------- #
# Spread-spectrum plan (deterministic, cached)
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=8)
def _band(size: int, band_lo: int, band_hi: int, seed: int):
    coords = np.array(
        [(u, v) for u in range(band_lo, band_hi) for v in range(band_lo, band_hi)],
        dtype=np.intp,
    )
    if len(coords) < _HEADER_COORDS + MAX_PAYLOAD_BYTES * 8:
        raise WatermarkError("frequency band too small for the configured capacity")
    rng = np.random.default_rng(seed)
    perm = rng.permutation(len(coords))
    coords = coords[perm]
    signs = rng.choice(np.array([-1.0, 1.0]), size=len(coords))
    return coords, signs


def _groups(start: int, stop: int, nbits: int) -> list[np.ndarray]:
    return np.array_split(np.arange(start, stop), nbits)


# --------------------------------------------------------------------------- #
# Canonical luminance helpers
# --------------------------------------------------------------------------- #


def _canonical_luma(luma_u8: np.ndarray, size: int) -> np.ndarray:
    img = Image.fromarray(luma_u8, "L").resize((size, size), Image.LANCZOS)
    return np.asarray(img).astype(np.float64)


def _upsample_delta(delta: np.ndarray, w: int, h: int) -> np.ndarray:
    img = Image.fromarray(delta.astype(np.float32), "F").resize((w, h), Image.BICUBIC)
    return np.asarray(img).astype(np.float64)


def _perceptual_mask(luma: np.ndarray, window: int, lo: float, hi: float) -> np.ndarray:
    mean = uniform_filter(luma, size=window)
    variance = uniform_filter(luma * luma, size=window) - mean * mean
    activity = np.sqrt(np.clip(variance, 0.0, None))
    normalised = activity / (float(activity.mean()) + 1e-6)
    return np.clip(normalised, lo, hi)


def _psnr(a: np.ndarray, b: np.ndarray) -> float:
    mse = float(np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2))
    return float("inf") if mse == 0 else 10.0 * float(np.log10(255.0**2 / mse))


# --------------------------------------------------------------------------- #
# ISS embed / detect over a set of coefficient groups
# --------------------------------------------------------------------------- #


def _iss_embed(D: np.ndarray, coords: np.ndarray, signs: np.ndarray,
               groups: list[np.ndarray], bits: np.ndarray, alpha: float) -> None:
    for i, g in enumerate(groups):
        c = coords[g]
        s = signs[g]
        root = math.sqrt(len(g))
        target = alpha if bits[i] else -alpha
        projection = float(np.sum(s * D[c[:, 0], c[:, 1]])) / root
        D[c[:, 0], c[:, 1]] += ((target - projection) / root) * s


def _iss_detect(D: np.ndarray, coords: np.ndarray, signs: np.ndarray,
                groups: list[np.ndarray]) -> np.ndarray:
    bits = np.zeros(len(groups), dtype=np.uint8)
    for i, g in enumerate(groups):
        c = coords[g]
        s = signs[g]
        bits[i] = 1 if np.sum(s * D[c[:, 0], c[:, 1]]) > 0 else 0
    return bits


# --------------------------------------------------------------------------- #
# Embed
# --------------------------------------------------------------------------- #


def embed(cover_path: str, payload: bytes, out_path: str,
          config: WatermarkConfig | None = None) -> WatermarkResult:
    """Embed ``payload`` into ``cover_path`` and save ``out_path``.

    ``payload`` is Brotli-compressed when that helps, so the ``MAX_PAYLOAD_BYTES``
    cap is on the *compressed* size.  The output PNG keeps the cover's aspect ratio
    (very large originals are downscaled to ``_MAX_WORK_SIZE`` on the longest side).
    """
    cfg = config or WatermarkConfig()
    body, flags = _compress(payload)
    if len(body) > MAX_PAYLOAD_BYTES:
        raise WatermarkError(
            f"payload is {len(body)} B after compression; max is {MAX_PAYLOAD_BYTES} B")

    protected = bytes(RSCodec(cfg.nsym).encode(bytearray(body))) if body else b""
    header_bits = _pack_header(len(body), cfg.nsym, flags)
    payload_bits = (np.unpackbits(np.frombuffer(protected, dtype=np.uint8))
                    if protected else np.zeros(0, dtype=np.uint8))

    coords, signs = _band(cfg.canonical_size, cfg.band_lo, cfg.band_hi, cfg.seed)
    if _HEADER_COORDS + len(payload_bits) > len(coords):
        raise WatermarkError("payload too large for this cover/config capacity")
    header_groups = _groups(0, _HEADER_COORDS, _HEADER_BITS)

    cover = ImageOps.exif_transpose(Image.open(cover_path)).convert("RGB")
    W, H = cover.size
    # Cap very large originals: it bounds the canonical->native->download resampling
    # ratio (which otherwise erodes the watermark band on high-resolution photos)
    # and matches what social platforms keep anyway.  Aspect ratio is preserved.
    if max(W, H) > _MAX_WORK_SIZE:
        scale = _MAX_WORK_SIZE / max(W, H)
        W, H = round(W * scale), round(H * scale)
        cover = cover.resize((W, H), Image.LANCZOS)
    # The watermark band reaches ``band_hi`` cycles, so the output image must be
    # large enough (>= ~2*band_hi) to represent it.  Upscale covers that are too
    # small; normal photos (>= canonical_size) are left at their native size.
    if min(W, H) < cfg.canonical_size:
        scale = cfg.canonical_size / min(W, H)
        W, H = round(W * scale), round(H * scale)
        cover = cover.resize((W, H), Image.LANCZOS)

    ycc = np.asarray(cover.convert("YCbCr")).astype(np.float64)
    Y = ycc[:, :, 0]

    Yc = _canonical_luma(Y.astype(np.uint8), cfg.canonical_size)
    D = dctn(Yc, norm="ortho")
    _iss_embed(D, coords, signs, header_groups, header_bits, cfg.strength)
    if len(payload_bits):
        payload_groups = _groups(_HEADER_COORDS, len(coords), len(payload_bits))
        payload_alpha = _payload_strength(cfg.strength, len(coords) - _HEADER_COORDS,
                                          len(payload_bits))
        _iss_embed(D, coords, signs, payload_groups, payload_bits, payload_alpha)

    delta_canon = idctn(D, norm="ortho") - Yc
    if cfg.perceptual_mask:
        delta_canon = delta_canon * _perceptual_mask(Yc, cfg.mask_window, cfg.mask_lo, cfg.mask_hi)
    delta_native = _upsample_delta(delta_canon.astype(np.float32), W, H)
    ycc[:, :, 0] = np.clip(Y + delta_native, 0, 255)
    stego = Image.fromarray(ycc.astype(np.uint8), "YCbCr").convert("RGB")
    stego.save(out_path)

    psnr = _psnr(np.asarray(cover), np.asarray(stego))
    return WatermarkResult(out_path=out_path, valid=True, payload=payload, psnr_db=psnr)


# --------------------------------------------------------------------------- #
# Extract
# --------------------------------------------------------------------------- #


def _decode_image(img: Image.Image, cfg: WatermarkConfig) -> WatermarkResult | None:
    """Try to decode a watermark from one (already oriented) image.

    Returns ``None`` if no magic+CRC-valid header or the RS decode fails, so the
    caller can try another candidate orientation.
    """
    Yc = _canonical_luma(np.asarray(img.convert("YCbCr"))[:, :, 0], cfg.canonical_size)
    D = dctn(Yc, norm="ortho")
    coords, signs = _band(cfg.canonical_size, cfg.band_lo, cfg.band_hi, cfg.seed)
    header_bits = _iss_detect(D, coords, signs, _groups(0, _HEADER_COORDS, _HEADER_BITS))
    valid, length, nsym, flags = _parse_header(header_bits)
    if not valid:
        return None

    protected_len = _rs_encoded_len(length, nsym)
    if protected_len == 0:
        return WatermarkResult(out_path=None, valid=True, payload=b"", psnr_db=float("nan"))
    nbits = protected_len * 8
    if _HEADER_COORDS + nbits > len(coords):
        return None
    payload_groups = _groups(_HEADER_COORDS, len(coords), nbits)
    payload_bits = _iss_detect(D, coords, signs, payload_groups)
    protected = np.packbits(payload_bits).tobytes()[:protected_len]

    try:
        body = bytes(RSCodec(nsym).decode(bytearray(protected))[0])[:length]
    except ReedSolomonError:
        return None
    decoded = _decompress(body, flags)
    if decoded is None:                       # corrupt/oversized compressed payload
        return None
    return WatermarkResult(out_path=None, valid=True, payload=decoded, psnr_db=float("nan"))


def extract(stego_path: str, config: WatermarkConfig | None = None) -> WatermarkResult:
    """Recover the payload from ``stego_path``.

    Applies the image's EXIF orientation and, if the direct read fails, retries
    the three 90-degree rotations (phones and some platforms bake in orientation
    changes) and the frequency bands used by previous releases (so images marked
    by an older version still verify).  ``WatermarkResult.valid`` is ``False``
    when no magic+CRC-consistent watermark is present (unmarked, cropped, or
    too-degraded image).
    """
    cfg = config or WatermarkConfig()
    img = ImageOps.exif_transpose(Image.open(stego_path)).convert("RGB")
    candidates = (
        img,
        img.rotate(90, expand=True),
        img.rotate(180, expand=True),
        img.rotate(270, expand=True),
    )
    configs = [cfg]
    configs += [replace(cfg, band_hi=bh) for bh in _LEGACY_BAND_HI if bh != cfg.band_hi]
    for cfg_try in configs:
        for candidate in candidates:
            result = _decode_image(candidate, cfg_try)
            if result is not None:
                return result
    return WatermarkResult(out_path=None, valid=False, payload=b"", psnr_db=float("nan"))


def embed_text(cover_path: str, text: str, out_path: str,
               config: WatermarkConfig | None = None) -> WatermarkResult:
    """Convenience wrapper: embed a UTF-8 string.

    Text is Brotli-compressed before embedding, so the effective limit is 512 B
    *after* compression — ordinary prose can run well over 512 raw bytes.  A hard
    ``MAX_TEXT_BYTES`` guard keeps incompressible input from exceeding capacity.
    """
    data = text.encode("utf-8")
    if len(data) > MAX_TEXT_BYTES:
        raise WatermarkError(f"text is {len(data)} B; max is {MAX_TEXT_BYTES} B")
    return embed(cover_path, data, out_path, config)


def embedded_size(text: str) -> tuple[int, bool]:
    """Bytes that would actually be embedded for ``text``, and whether Brotli helped.

    Handy for a UI byte counter: the cap (:data:`MAX_PAYLOAD_BYTES`) applies to this
    *compressed* size, not the raw character count.
    """
    body, flags = _compress(text.encode("utf-8"))
    return len(body), bool(flags & _FLAG_COMPRESSED)
