"""Shared configuration for the ANS-STC steganography pipeline.

All tunable parameters live here so the console core, the GUI and the channel
simulator stay in sync.  The values are deliberately conservative: they favour a
*mathematically exact* round-trip (Hito 1) over maximum capacity.
"""

from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------------- #
# rANS entropy coder
# --------------------------------------------------------------------------- #
SCALE_BITS: int = 12          # frequency total M = 2**SCALE_BITS = 4096
RANS_L: int = 1 << 23         # lower bound of the normalised rANS interval
RANS_MASK32: int = 0xFFFFFFFF  # 32-bit wrap mask

# --------------------------------------------------------------------------- #
# Payload container
# --------------------------------------------------------------------------- #
PAYLOAD_MAGIC: bytes = b"AS1"  # container magic (ANS-STC v1)
PTYPE_TEXT: int = 0
PTYPE_FILE: int = 1
PTYPE_IMAGE: int = 2

# --------------------------------------------------------------------------- #
# Syndrome-Trellis Codes
# --------------------------------------------------------------------------- #
STC_HEIGHT: int = 8           # constraint height h  ->  2**h trellis states
STC_WIDTH_HEADER: int = 2     # fixed width for the tiny signalling header
# Upper bound on the adaptive payload width w.  A smaller cap means each syndrome
# bit depends on fewer coefficients, which curbs the STC "avalanche" (one flipped
# coefficient corrupts up to h message bits) and makes the payload far easier for
# Reed-Solomon to repair after a noisy channel -- at the price of touching more
# coefficients.  Large payloads naturally use a small w regardless of this cap.
MAX_PAYLOAD_WIDTH: int = 4
HEADER_COEFFS: int = 512      # cover elements reserved for the header region
HEADER_RAW_BYTES: int = 11    # SYNC(2)+VER(1)+W(1)+NSYM(1)+LEN(4)+CRC(2)

# --------------------------------------------------------------------------- #
# DCT / quantization
# --------------------------------------------------------------------------- #
BLOCK: int = 8                # DCT block size (8x8, JPEG grid aligned)
# Quantization step applied to the embedded mid-frequency coefficients.  It must
# exceed JPEG's own quantization step at the mid frequencies so the embedded
# parity survives recompression; a large step also gives the parity enough margin
# to survive the spatial<->frequency rounding of a lossless (PNG) round-trip.
QUANT_STEP: int = 48

# Zig-zag indices (0..63) considered "mid frequency".  DC (0) and the highest
# frequencies are excluded: the former is too visible, the latter is destroyed
# first by JPEG.  Order matters and is shared by embedder and extractor.
MID_FREQ_ZIGZAG: tuple[int, ...] = (
    5, 6, 7, 12, 13, 14, 15, 19, 20, 21, 22, 26, 27, 28,
)

# --------------------------------------------------------------------------- #
# Reed-Solomon ECC
# --------------------------------------------------------------------------- #
RS_NSYM: int = 120            # parity bytes per 255-byte block (corrects 60)


@dataclass(frozen=True)
class StegoConfig:
    """Bundle of every parameter that must match between hide and extract."""

    scale_bits: int = SCALE_BITS
    rs_nsym: int = RS_NSYM
    stc_height: int = STC_HEIGHT
    stc_width_header: int = STC_WIDTH_HEADER
    max_payload_width: int = MAX_PAYLOAD_WIDTH
    header_coeffs: int = HEADER_COEFFS
    header_raw_bytes: int = HEADER_RAW_BYTES
    block: int = BLOCK
    quant_step: int = QUANT_STEP
    mid_freq_zigzag: tuple[int, ...] = MID_FREQ_ZIGZAG
    channel: str = "G"        # colour plane used for embedding (R/G/B or L)

    @property
    def coeffs_per_block(self) -> int:
        return len(self.mid_freq_zigzag)

    # -- ready-made presets ------------------------------------------------ #
    @classmethod
    def max_quality(cls) -> "StegoConfig":
        """Best PSNR for a lossless (PNG) work-flow; minimal JPEG resilience."""
        return cls(quant_step=16, rs_nsym=32, channel="G", max_payload_width=64)

    @classmethod
    def balanced(cls) -> "StegoConfig":
        """Middle trade-off: ~40 dB, survives high-quality JPEG (Q>=90)."""
        return cls(quant_step=32, rs_nsym=80, channel="G", max_payload_width=6)

    @classmethod
    def robust(cls) -> "StegoConfig":
        """Default: maximum channel resilience, survives social-media JPEG (Q75-Q90)."""
        return cls(quant_step=48, rs_nsym=120, channel="G", max_payload_width=4)
