"""Noisy-channel simulator (auxiliary, outside the GUI).

Validates how well an embedded payload survives lossy transmission — chiefly the
JPEG recompression applied by social networks.  For each quality level it reports

* **coeff BER** – fraction of embedded coefficient parities flipped by the
  channel (the raw error the STC layer sees);
* **msg BER**   – fraction of payload message bits wrong *after* the STC
  "avalanche" amplifies those flips (this is what Reed-Solomon must repair);
* **recovered** – whether the full ANS+ECC+STC chain reproduced the payload.

Run directly::

    python -m ans_stc.channel_simulator [cover_image.png]
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
from dataclasses import dataclass

import numpy as np
from PIL import Image

from .config import StegoConfig
from .payload_manager import bytes_to_bits, encode_payload, pack_text, unpack_payload
from .pipeline import extract, hide
from .stc_core import stc_extract
from .transform_engine import DctCarrier, ImageCarrier


# --------------------------------------------------------------------------- #
# Channel models
# --------------------------------------------------------------------------- #


def jpeg_channel(array: np.ndarray, quality: int, subsampling: int = 0) -> np.ndarray:
    """Round-trip ``array`` through JPEG at the given quality (0=4:4:4 chroma)."""
    mode = "L" if array.ndim == 2 else "RGB"
    buf = io.BytesIO()
    Image.fromarray(array, mode).save(
        buf, format="JPEG", quality=int(quality), subsampling=subsampling
    )
    buf.seek(0)
    return np.asarray(Image.open(buf).convert(mode))


def awgn_channel(array: np.ndarray, sigma: float, seed: int = 0) -> np.ndarray:
    """Add white Gaussian noise of standard deviation ``sigma`` (in gray levels)."""
    rng = np.random.default_rng(seed)
    noisy = array.astype(np.float64) + rng.normal(0.0, sigma, array.shape)
    return np.clip(np.round(noisy), 0, 255).astype(np.uint8)


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #


@dataclass
class ChannelResult:
    quality: int
    coeff_ber: float
    msg_ber: float
    recovered: bool
    note: str = ""


def _parities(array: np.ndarray, config: StegoConfig) -> tuple[np.ndarray, int]:
    mode = "L" if array.ndim == 2 else "RGB"
    carrier = DctCarrier(ImageCarrier(array, mode, config.channel).plane(), config)
    return carrier.cover_parities(), carrier.n_coeffs


def evaluate(
    cover_path: str,
    container: bytes,
    config: StegoConfig | None = None,
    qualities: tuple[int, ...] = (70, 80, 90, 95),
    subsampling: int = 0,
) -> list[ChannelResult]:
    """Embed ``container`` then measure survival across JPEG qualities."""
    config = config or StegoConfig()
    workdir = tempfile.mkdtemp(prefix="ans_stc_chan_")
    stego_path = os.path.join(workdir, "stego.png")
    result = hide(cover_path, container, stego_path, config)

    stego_array = np.asarray(Image.open(stego_path))
    if stego_array.ndim == 3:
        stego_array = stego_array[:, :, :3]

    protected, _ = encode_payload(container, config.scale_bits, config.rs_nsym)
    ref_bits = bytes_to_bits(protected)
    length = len(protected)

    pre_parity, n = _parities(stego_array, config)
    n1 = config.header_coeffs
    width = result.payload_width
    m2 = (n - n1) // width
    used2 = m2 * width
    reference = unpack_payload(container)

    rows: list[ChannelResult] = []
    for q in qualities:
        received = jpeg_channel(stego_array, q, subsampling)
        post_parity, n_post = _parities(received, config)

        if n_post != n:
            rows.append(ChannelResult(q, float("nan"), float("nan"), False, "geometry drift"))
            continue

        coeff_ber = float(np.mean(pre_parity[n1 : n1 + used2] != post_parity[n1 : n1 + used2]))
        ext_bits = stc_extract(post_parity[n1 : n1 + used2], m2, config.stc_height, width)
        msg_ber = float(np.mean(ext_bits[: 8 * length] != ref_bits[: 8 * length]))

        recv_path = os.path.join(workdir, f"recv_q{q}.png")
        Image.fromarray(received, "RGB" if received.ndim == 3 else "L").save(recv_path)
        note = ""
        try:
            got = extract(recv_path, config)
            recovered = (
                got.payload.ptype == reference.ptype and got.payload.data == reference.data
            )
        except Exception as exc:  # noqa: BLE001 - report any failure verbatim
            recovered = False
            note = type(exc).__name__
        rows.append(ChannelResult(q, coeff_ber, msg_ber, recovered, note))

    return rows


def print_report(rows: list[ChannelResult], title: str = "") -> None:
    if title:
        print(title)
    print(f"  {'Quality':>7} | {'coeff BER':>9} | {'msg BER':>8} | {'recovered':>9} | note")
    print("  " + "-" * 54)
    for r in rows:
        mark = "YES" if r.recovered else "no"
        print(
            f"  {r.quality:>7} | {r.coeff_ber:>8.3%} | {r.msg_ber:>7.3%} | "
            f"{mark:>9} | {r.note}"
        )


# --------------------------------------------------------------------------- #
# Demo entry point
# --------------------------------------------------------------------------- #


def _synthetic_cover(size: int = 448, seed: int = 1) -> np.ndarray:
    """A multi-scale textured cover (realistic photos have rich mid-band energy)."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:size, 0:size]
    base = (
        120
        + 45 * np.sin(xx / 21.0)
        + 30 * np.cos(yy / 17.0)
        + 22 * np.sin((xx + yy) / 9.0)
        + 16 * np.cos((xx - yy) / 5.0)
        + 12 * np.sin(xx / 3.0) * np.cos(yy / 3.0)
    )
    im = np.clip(base + rng.normal(0, 16, (size, size)), 0, 255).astype(np.uint8)
    return np.stack(
        [im, np.clip(im * 0.9 + 15, 0, 255).astype(np.uint8), np.clip(im * 1.05, 0, 255).astype(np.uint8)],
        axis=-1,
    )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    workdir = tempfile.mkdtemp(prefix="ans_stc_demo_")
    if argv:
        cover_path = argv[0]
    else:
        cover_path = os.path.join(workdir, "cover.png")
        Image.fromarray(_synthetic_cover(), "RGB").save(cover_path)
        print(f"[i] no cover given - generated a synthetic one at {cover_path}")

    container = pack_text("ANS-STC channel test: la ciberseguridad protege la verdad. " * 4)

    for name, config in (
        ("max_quality", StegoConfig.max_quality()),
        ("balanced", StegoConfig.balanced()),
        ("robust (default)", StegoConfig.robust()),
    ):
        rows = evaluate(cover_path, container, config)
        print()
        print_report(rows, title=f"Preset: {name}  [channel={config.channel} qstep={config.quant_step} rs_nsym={config.rs_nsym}]")
    print()
    print("Note: 'coeff BER' is the raw parity-flip rate from the channel; the STC")
    print("avalanche amplifies it into 'msg BER', which Reed-Solomon then repairs.")
    print("Survival depends on cover texture and the quant-step vs JPEG-step match,")
    print("so results vary per image/quality -- use this tool to pick a preset.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
