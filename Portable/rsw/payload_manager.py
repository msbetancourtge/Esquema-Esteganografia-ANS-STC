"""Payload pre-processing: dynamic packing, rANS entropy coding and ECC.

Pipeline for the *hide* direction::

    user input ──pack──▶ container ──rANS──▶ compressed ──Reed-Solomon──▶ bytes

and the exact inverse for *extract*.  The rANS coder is a static, byte-wise
range Asymmetric Numeral System (rANS) with 8-bit renormalisation, following
the classic ryg_rans construction.  Reed-Solomon (via :mod:`reedsolo`) wraps the
compressed stream so that the bit bursts produced by the STC "avalanche" during
a noisy channel can be repaired.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np
from reedsolo import ReedSolomonError, RSCodec

from .config import (
    PAYLOAD_MAGIC,
    PTYPE_FILE,
    PTYPE_IMAGE,
    PTYPE_TEXT,
    RANS_L,
    RANS_MASK32,
    SCALE_BITS,
)

# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class PayloadError(Exception):
    """Raised when a payload cannot be packed, unpacked, decoded or repaired."""


# --------------------------------------------------------------------------- #
# Dynamic payload container
# --------------------------------------------------------------------------- #


@dataclass
class Payload:
    """A decoded payload plus the metadata needed to present/save it."""

    ptype: int
    name: str
    data: bytes

    @property
    def kind(self) -> str:
        return {PTYPE_TEXT: "text", PTYPE_FILE: "file", PTYPE_IMAGE: "image"}.get(
            self.ptype, "unknown"
        )

    def as_text(self) -> str:
        return self.data.decode("utf-8", errors="replace")


def pack_payload(ptype: int, data: bytes, name: str = "") -> bytes:
    """Serialise a payload into the normalised byte container.

    Layout::

        MAGIC(3) | PTYPE(1) | NAME_LEN(2, BE) | NAME(utf-8) | DATA
    """
    name_bytes = name.encode("utf-8")
    if len(name_bytes) > 0xFFFF:
        raise PayloadError("payload name is too long")
    return b"".join(
        (
            PAYLOAD_MAGIC,
            struct.pack(">B", ptype & 0xFF),
            struct.pack(">H", len(name_bytes)),
            name_bytes,
            data,
        )
    )


def unpack_payload(blob: bytes) -> Payload:
    """Inverse of :func:`pack_payload` with strict header validation."""
    if len(blob) < 6 or blob[:3] != PAYLOAD_MAGIC:
        raise PayloadError("payload container magic mismatch")
    ptype = blob[3]
    (name_len,) = struct.unpack(">H", blob[4:6])
    if len(blob) < 6 + name_len:
        raise PayloadError("payload container truncated")
    name = blob[6 : 6 + name_len].decode("utf-8", errors="replace")
    data = blob[6 + name_len :]
    return Payload(ptype=ptype, name=name, data=data)


def pack_text(text: str) -> bytes:
    return pack_payload(PTYPE_TEXT, text.encode("utf-8"))


def pack_file(path: str, data: bytes, image: bool = False) -> bytes:
    import os

    ptype = PTYPE_IMAGE if image else PTYPE_FILE
    return pack_payload(ptype, data, name=os.path.basename(path))


# --------------------------------------------------------------------------- #
# rANS frequency model
# --------------------------------------------------------------------------- #


def _build_frequencies(data: bytes, scale_bits: int) -> np.ndarray:
    """Return a length-256 array of frequencies summing exactly to ``2**scale_bits``.

    Every symbol that occurs keeps a strictly positive frequency; symbols that
    never occur stay at zero.  The largest bucket absorbs the rounding residual.
    """
    total = 1 << scale_bits
    counts = np.bincount(np.frombuffer(data, dtype=np.uint8), minlength=256).astype(
        np.int64
    )
    n = counts.sum()
    if n == 0:
        return np.zeros(256, dtype=np.int64)

    # Proportional allocation, then guarantee freq >= 1 for present symbols.
    freqs = np.maximum((counts * total) // n, 1)
    freqs[counts == 0] = 0

    # Fix the total so it equals ``total`` exactly by nudging the fattest bucket.
    diff = total - int(freqs.sum())
    if diff != 0:
        # Distribute the residual onto the most frequent symbol(s) while keeping
        # every present frequency >= 1.
        order = np.argsort(counts)[::-1]
        i = 0
        step = 1 if diff > 0 else -1
        remaining = abs(diff)
        while remaining > 0:
            sym = order[i % int((counts > 0).sum())]
            if step < 0 and freqs[sym] <= 1:
                i += 1
                continue
            freqs[sym] += step
            remaining -= 1
            i += 1
    assert freqs.sum() == total
    return freqs


def _cum_and_lookup(freqs: np.ndarray, scale_bits: int):
    """Return cumulative-start array and a slot->symbol lookup table."""
    total = 1 << scale_bits
    cum = np.zeros(256, dtype=np.int64)
    np.cumsum(freqs[:-1], out=cum[1:])
    slot2sym = np.zeros(total, dtype=np.int32)
    for sym in np.nonzero(freqs)[0]:
        slot2sym[cum[sym] : cum[sym] + freqs[sym]] = sym
    return cum, slot2sym


# --------------------------------------------------------------------------- #
# rANS encode / decode
# --------------------------------------------------------------------------- #


def rans_encode(data: bytes, scale_bits: int = SCALE_BITS) -> bytes:
    """Compress ``data`` with static byte-wise rANS.

    The returned blob is self-describing: it embeds the frequency table so that
    :func:`rans_decode` needs no side information.
    """
    freqs = _build_frequencies(data, scale_bits)
    cum, _ = _cum_and_lookup(freqs, scale_bits) if data else (np.zeros(256, np.int64), None)

    stream = bytearray()
    if data:
        freqs_i = freqs.astype(np.int64)
        state = RANS_L
        x_max_factor = (RANS_L >> scale_bits) << 8
        # Encode symbols in reverse so decoding runs forward.
        for sym in reversed(data):
            f = int(freqs_i[sym])
            c = int(cum[sym])
            x_max = x_max_factor * f
            while state >= x_max:
                stream.append(state & 0xFF)
                state >>= 8
            state = ((state // f) << scale_bits) + (state % f) + c
        # Flush the 32-bit state, low byte first.
        for _ in range(4):
            stream.append(state & 0xFF)
            state >>= 8

    # --- serialise header + stream ---------------------------------------- #
    present = np.nonzero(freqs)[0]
    header = bytearray()
    header += PAYLOAD_MAGIC
    header += struct.pack(">B", scale_bits)
    header += struct.pack(">I", len(data))
    header += struct.pack(">H", len(present))
    for sym in present:
        header += struct.pack(">BH", int(sym), int(freqs[sym]))
    header += struct.pack(">I", len(stream))
    return bytes(header) + bytes(stream)


def rans_decode(blob: bytes) -> bytes:
    """Inverse of :func:`rans_encode`."""
    if len(blob) < 10 or blob[:3] != PAYLOAD_MAGIC:
        raise PayloadError("rANS stream magic mismatch")
    off = 3
    scale_bits = blob[off]
    off += 1
    (orig_len,) = struct.unpack(">I", blob[off : off + 4])
    off += 4
    (num_sym,) = struct.unpack(">H", blob[off : off + 2])
    off += 2

    freqs = np.zeros(256, dtype=np.int64)
    for _ in range(num_sym):
        sym, f = struct.unpack(">BH", blob[off : off + 3])
        freqs[sym] = f
        off += 3
    (stream_len,) = struct.unpack(">I", blob[off : off + 4])
    off += 4
    stream = blob[off : off + stream_len]
    if len(stream) != stream_len:
        raise PayloadError("rANS stream truncated")

    if orig_len == 0:
        return b""

    if int(freqs.sum()) != (1 << scale_bits):
        raise PayloadError("rANS frequency table is inconsistent")

    cum, slot2sym = _cum_and_lookup(freqs, scale_bits)
    mask = (1 << scale_bits) - 1

    # Read the 32-bit state from the end of the stream (reverse of the flush).
    pos = len(stream)
    state = 0
    for _ in range(4):
        pos -= 1
        state = ((state << 8) | stream[pos]) & RANS_MASK32

    freqs_i = freqs
    out = bytearray(orig_len)
    for i in range(orig_len):
        slot = state & mask
        sym = int(slot2sym[slot])
        f = int(freqs_i[sym])
        c = int(cum[sym])
        state = f * (state >> scale_bits) + slot - c
        while state < RANS_L:
            pos -= 1
            state = ((state << 8) | stream[pos]) & RANS_MASK32
        out[i] = sym
    return bytes(out)


# --------------------------------------------------------------------------- #
# Reed-Solomon ECC layer
# --------------------------------------------------------------------------- #


def ecc_encode(data: bytes, nsym: int) -> bytes:
    """Wrap ``data`` with Reed-Solomon parity (chunked at 255 bytes)."""
    if nsym <= 0:
        return data
    rsc = RSCodec(nsym)
    return bytes(rsc.encode(bytearray(data)))


def ecc_decode(data: bytes, nsym: int) -> bytes:
    """Repair and strip Reed-Solomon parity.

    Raises :class:`PayloadError` when the number of byte errors exceeds the
    correcting capability (``nsym // 2`` per 255-byte block).
    """
    if nsym <= 0:
        return data
    rsc = RSCodec(nsym)
    try:
        decoded, _, _ = rsc.decode(bytearray(data))
    except ReedSolomonError as exc:  # pragma: no cover - exercised via channel sim
        raise PayloadError(f"Reed-Solomon could not repair the payload: {exc}") from exc
    return bytes(decoded)


# --------------------------------------------------------------------------- #
# High-level façade
# --------------------------------------------------------------------------- #


@dataclass
class EncodeStats:
    raw_bytes: int
    compressed_bytes: int
    ecc_bytes: int

    @property
    def compression_ratio(self) -> float:
        return self.raw_bytes / self.compressed_bytes if self.compressed_bytes else 0.0


def encode_payload(
    container: bytes, scale_bits: int, rs_nsym: int
) -> tuple[bytes, EncodeStats]:
    """Container bytes -> ANS -> Reed-Solomon -> channel-ready bytes."""
    compressed = rans_encode(container, scale_bits)
    protected = ecc_encode(compressed, rs_nsym)
    stats = EncodeStats(
        raw_bytes=len(container),
        compressed_bytes=len(compressed),
        ecc_bytes=len(protected),
    )
    return protected, stats


def decode_payload(protected: bytes, rs_nsym: int) -> bytes:
    """Inverse of :func:`encode_payload` returning the original container bytes."""
    compressed = ecc_decode(protected, rs_nsym)
    return rans_decode(compressed)


# --------------------------------------------------------------------------- #
# bytes <-> bit helpers (MSB first) used by the STC layer
# --------------------------------------------------------------------------- #


def bytes_to_bits(data: bytes) -> np.ndarray:
    return np.unpackbits(np.frombuffer(data, dtype=np.uint8))


def bits_to_bytes(bits: np.ndarray) -> bytes:
    bits = np.asarray(bits, dtype=np.uint8).ravel()
    pad = (-len(bits)) % 8
    if pad:
        bits = np.concatenate([bits, np.zeros(pad, dtype=np.uint8)])
    return np.packbits(bits).tobytes()
