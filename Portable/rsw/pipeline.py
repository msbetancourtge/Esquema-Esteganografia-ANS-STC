"""End-to-end hide / extract orchestration.

Embedding layout of the selected DCT coefficients::

    [ 0 .............. header_coeffs )   header region  (repetition-coded)
    [ header_coeffs ............... n )   payload region (STC + Reed-Solomon)

The header region carries a tiny, self-checked descriptor (payload width ``w``,
Reed-Solomon strength and payload length).  It is *not* STC-coded: STC's
error "avalanche" would make the header the most fragile part of the image, so
instead the 11-byte descriptor is written directly onto coefficient parities and
**repeated many times**; extraction majority-votes the copies, which tolerates a
very high channel error rate.  The payload region keeps the STC + J-UNIWARD +
Reed-Solomon chain::

    payload  ─ANS─  compress  ─RS─  protect  ─STC/J-UNIWARD─  DCT coefficients
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

from .config import StegoConfig
from .cost_calculator import juniward_costs
from .payload_manager import (
    Payload,
    PayloadError,
    bits_to_bytes,
    bytes_to_bits,
    decode_payload,
    encode_payload,
    pack_file,
    pack_text,
    unpack_payload,
)
from .stc_core import stc_embed, stc_extract
from .transform_engine import DctCarrier, ImageCarrier

_HEADER_SYNC = 0xA55A
_HEADER_VERSION = 1


class CapacityError(PayloadError):
    """Raised when the payload does not fit in the chosen cover image."""


# --------------------------------------------------------------------------- #
# Header helpers  (repetition-coded, majority-voted)
# --------------------------------------------------------------------------- #


def _crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _build_header(width: int, nsym: int, length: int) -> bytes:
    body = struct.pack(">HBBBI", _HEADER_SYNC, _HEADER_VERSION, width, nsym, length)
    return body + struct.pack(">H", _crc16(body))


def _parse_header(raw: bytes) -> tuple[int, int, int]:
    if len(raw) < 11:
        raise PayloadError("header too short")
    body, crc = raw[:9], raw[9:11]
    sync, version, width, nsym, length = struct.unpack(">HBBBI", body)
    if sync != _HEADER_SYNC:
        raise PayloadError("header sync mismatch (not a stego image or wrong settings)")
    if struct.unpack(">H", crc)[0] != _crc16(body):
        raise PayloadError("header CRC mismatch (image corrupted beyond repair)")
    if version != _HEADER_VERSION:
        raise PayloadError(f"unsupported stego version {version}")
    return width, nsym, length


def _header_repeats(config: StegoConfig) -> tuple[int, int]:
    """Return ``(bits_per_copy, number_of_copies)`` for the header region."""
    bits = config.header_raw_bytes * 8
    copies = config.header_coeffs // bits
    if copies < 1:
        raise CapacityError("header region too small for even one header copy")
    if copies % 2 == 0:  # keep it odd so majority voting never ties
        copies -= 1
    return bits, copies


def _encode_header_parities(width: int, nsym: int, length: int, config: StegoConfig) -> np.ndarray:
    bits = bytes_to_bits(_build_header(width, nsym, length))
    _, copies = _header_repeats(config)
    return np.tile(bits, copies)


def _decode_header(parity: np.ndarray, config: StegoConfig) -> tuple[int, int, int]:
    bits, copies = _header_repeats(config)
    votes = parity[: bits * copies].reshape(copies, bits)
    majority = (votes.sum(axis=0) * 2 > copies).astype(np.uint8)
    return _parse_header(bits_to_bytes(majority))


# --------------------------------------------------------------------------- #
# Result records
# --------------------------------------------------------------------------- #


@dataclass
class HideResult:
    out_path: str
    payload_bytes: int
    compressed_bytes: int
    ecc_bytes: int
    payload_width: int
    coeffs_total: int
    coeffs_changed: int
    psnr_db: float
    capacity_bytes: int

    @property
    def change_rate(self) -> float:
        return self.coeffs_changed / self.coeffs_total if self.coeffs_total else 0.0


@dataclass
class ExtractResult:
    payload: Payload
    ecc_bytes: int
    payload_width: int
    message_bits: int


# --------------------------------------------------------------------------- #
# Capacity
# --------------------------------------------------------------------------- #


def capacity_bytes(cover_path: str, config: StegoConfig | None = None) -> int:
    """Maximum ANS+ECC byte length that fits (before payload compression)."""
    config = config or StegoConfig()
    carrier = DctCarrier(ImageCarrier.load(cover_path, config.channel).plane(), config)
    n2 = carrier.n_coeffs - config.header_coeffs
    if n2 <= 0:
        return 0
    return max(0, n2 // 8)


# --------------------------------------------------------------------------- #
# Hide
# --------------------------------------------------------------------------- #


def hide(cover_path: str, container: bytes, out_path: str, config: StegoConfig | None = None) -> HideResult:
    """Embed a packed payload container into ``cover_path`` and write ``out_path``."""
    config = config or StegoConfig()

    protected, stats = encode_payload(container, config.scale_bits, config.rs_nsym)
    length = len(protected)
    payload_bits = bytes_to_bits(protected)

    carrier_img = ImageCarrier.load(cover_path, config.channel)
    plane = carrier_img.plane()
    carrier = DctCarrier(plane, config)
    n = carrier.n_coeffs
    n1 = config.header_coeffs
    if n <= n1:
        raise CapacityError("cover image is too small for the header region")

    n2 = n - n1
    max_bytes = n2 // 8
    if length > max_bytes:
        raise CapacityError(
            f"payload needs {length} B but cover holds at most {max_bytes} B "
            f"(image too small or payload too large)"
        )

    # Adaptive payload width: as wide as possible (least distortion) while still
    # fitting, but capped so each syndrome bit stays a parity over few
    # coefficients -> the STC avalanche stays small enough for Reed-Solomon.
    natural_width = n2 // (8 * length)
    width = max(1, min(config.max_payload_width, natural_width))

    costs = juniward_costs(plane, config)
    cover_parity = carrier.cover_parities()

    # --- payload region --------------------------------------------------- #
    m2 = n2 // width
    msg2 = np.zeros(m2, dtype=np.uint8)
    msg2[: payload_bits.size] = payload_bits
    used2 = m2 * width
    y2 = stc_embed(
        cover_parity[n1 : n1 + used2],
        costs[n1 : n1 + used2],
        msg2,
        h=config.stc_height,
        w=width,
    )

    # --- header region (repetition-coded direct parity) ------------------- #
    header_parity = _encode_header_parities(width, config.rs_nsym, length, config)

    # --- write parities back into the DCT and reconstruct ----------------- #
    target = cover_parity.copy()
    target[: header_parity.size] = header_parity
    target[n1 : n1 + used2] = y2[:used2]
    changed = carrier.embed_parities(target)

    stego_array = carrier_img.with_plane(carrier.to_plane())
    carrier_img.save(out_path, stego_array)

    psnr = _psnr(carrier_img.array, stego_array)
    return HideResult(
        out_path=out_path,
        payload_bytes=stats.raw_bytes,
        compressed_bytes=stats.compressed_bytes,
        ecc_bytes=stats.ecc_bytes,
        payload_width=width,
        coeffs_total=n,
        coeffs_changed=changed,
        psnr_db=psnr,
        capacity_bytes=max_bytes,
    )


# --------------------------------------------------------------------------- #
# Extract
# --------------------------------------------------------------------------- #


def extract(stego_path: str, config: StegoConfig | None = None) -> ExtractResult:
    """Recover the payload from a stego image."""
    config = config or StegoConfig()

    carrier_img = ImageCarrier.load(stego_path, config.channel)
    carrier = DctCarrier(carrier_img.plane(), config)
    n = carrier.n_coeffs
    n1 = config.header_coeffs
    if n <= n1:
        raise PayloadError("image is too small to contain a stego header")

    parity = carrier.cover_parities()

    # --- header region (majority-voted) ----------------------------------- #
    width, nsym, length = _decode_header(parity[:n1], config)

    # --- payload region --------------------------------------------------- #
    n2 = n - n1
    m2 = n2 // width
    payload_bits = stc_extract(parity[n1 : n1 + m2 * width], m2, config.stc_height, width)
    protected = bits_to_bytes(payload_bits[: 8 * length])
    container = decode_payload(protected, nsym)
    payload = unpack_payload(container)

    return ExtractResult(
        payload=payload,
        ecc_bytes=length,
        payload_width=width,
        message_bits=8 * length,
    )


# --------------------------------------------------------------------------- #
# Convenience wrappers used by the CLI and GUI
# --------------------------------------------------------------------------- #


def hide_text(cover_path: str, text: str, out_path: str, config: StegoConfig | None = None) -> HideResult:
    return hide(cover_path, pack_text(text), out_path, config)


def hide_file(cover_path: str, file_path: str, out_path: str, image: bool = False, config: StegoConfig | None = None) -> HideResult:
    with open(file_path, "rb") as fh:
        data = fh.read()
    return hide(cover_path, pack_file(file_path, data, image=image), out_path, config)


def _psnr(a: np.ndarray, b: np.ndarray) -> float:
    mse = float(np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2))
    if mse == 0:
        return float("inf")
    return 10.0 * float(np.log10(255.0**2 / mse))
