"""Tests for the payload manager: rANS, Reed-Solomon and the container."""

from __future__ import annotations

import os

import numpy as np
import pytest

from ans_stc import payload_manager as pm
from ans_stc.config import PTYPE_FILE, PTYPE_TEXT


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"A",
        b"AAAAAAAAAAAAAAAA",
        b"Hello, robust steganography!",
        bytes(range(256)),
        ("El rapido zorro marron. " * 300).encode("utf-8"),
        os.urandom(4096),
    ],
)
def test_rans_roundtrip(data: bytes) -> None:
    assert pm.rans_decode(pm.rans_encode(data)) == data


def test_rans_compresses_repetitive_data() -> None:
    data = (b"steganography " * 500)
    encoded = pm.rans_encode(data)
    assert len(encoded) < len(data)


def test_ecc_corrects_errors() -> None:
    data = os.urandom(200)
    nsym = 32  # corrects up to 16 byte errors per block
    protected = bytearray(pm.ecc_encode(data, nsym))
    rng = np.random.default_rng(0)
    positions = rng.choice(len(protected), size=16, replace=False)
    for p in positions:
        protected[p] ^= 0xFF
    assert pm.ecc_decode(bytes(protected), nsym) == data


def test_ecc_raises_when_overwhelmed() -> None:
    data = os.urandom(100)
    nsym = 16  # corrects only 8 byte errors
    protected = bytearray(pm.ecc_encode(data, nsym))
    for p in range(30):  # far beyond capacity
        protected[p] ^= 0xFF
    with pytest.raises(pm.PayloadError):
        pm.ecc_decode(bytes(protected), nsym)


def test_container_roundtrip() -> None:
    blob = pm.pack_payload(PTYPE_FILE, b"\x00\x01\x02binary", name="report.pdf")
    payload = pm.unpack_payload(blob)
    assert payload.ptype == PTYPE_FILE
    assert payload.name == "report.pdf"
    assert payload.data == b"\x00\x01\x02binary"


def test_pack_text_roundtrip() -> None:
    payload = pm.unpack_payload(pm.pack_text("acentúación ñ 你好"))
    assert payload.ptype == PTYPE_TEXT
    assert payload.as_text() == "acentúación ñ 你好"


def test_full_encode_decode_pipeline() -> None:
    container = pm.pack_text("payload through ANS + Reed-Solomon")
    protected, stats = pm.encode_payload(container, scale_bits=12, rs_nsym=48)
    assert stats.ecc_bytes == len(protected)
    assert pm.decode_payload(protected, rs_nsym=48) == container


def test_bits_roundtrip() -> None:
    data = os.urandom(64)
    bits = pm.bytes_to_bits(data)
    assert set(np.unique(bits)).issubset({0, 1})
    assert pm.bits_to_bytes(bits) == data


def test_bad_magic_raises() -> None:
    with pytest.raises(pm.PayloadError):
        pm.rans_decode(b"XX not a valid stream")
