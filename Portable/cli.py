#!/usr/bin/env python3
"""Console core for the ANS-STC steganography system (Hito 1).

Runs the complete cycle without any GUI::

    # hide plain text
    python cli.py hide  --cover cover.png --text "secret message" --out stego.png

    # hide an arbitrary file (PDF, txt, ...) or a small image
    python cli.py hide  --cover cover.png --file report.pdf     --out stego.png
    python cli.py hide  --cover cover.png --image logo.png      --out stego.png

    # recover
    python cli.py extract --stego stego.png --out-dir ./recovered

    # capacity / robustness helpers
    python cli.py capacity --cover cover.png
    python cli.py channel  --cover cover.png

    # resize-proof watermark, up to 512 bytes (survives Facebook / Instagram / WhatsApp)
    python cli.py watermark --cover cover.png --text "authentic:mike-2026" --out marked.png
    python cli.py verify    --stego downloaded_from_facebook.jpg

    # steganalysis benchmark (detectability P_E / AUC) for the report
    python cli.py steganalysis --scheme watermark --images ./photos --count 60
    python cli.py steganalysis --scheme stego
"""

from __future__ import annotations

import argparse
import os
import sys

from ans_stc.config import StegoConfig
from ans_stc.payload_manager import PTYPE_TEXT, PayloadError
from ans_stc.pipeline import (
    CapacityError,
    capacity_bytes,
    extract,
    hide_file,
    hide_text,
)
from ans_stc.robust_watermark import (
    MAX_PAYLOAD_BYTES,
    WatermarkConfig,
    WatermarkError,
)
from ans_stc.robust_watermark import embed as wm_embed
from ans_stc.robust_watermark import embed_text as wm_embed_text
from ans_stc.robust_watermark import extract as wm_extract

_PRESETS = {
    "max": StegoConfig.max_quality,
    "balanced": StegoConfig.balanced,
    "robust": StegoConfig.robust,
}


def _config_from_args(args: argparse.Namespace) -> StegoConfig:
    return _PRESETS[args.preset]()


def _cmd_hide(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    try:
        if args.text is not None:
            result = hide_text(args.cover, args.text, args.out, config)
        elif args.text_file is not None:
            with open(args.text_file, "r", encoding="utf-8") as fh:
                result = hide_text(args.cover, fh.read(), args.out, config)
        elif args.file is not None:
            result = hide_file(args.cover, args.file, args.out, image=False, config=config)
        elif args.image is not None:
            result = hide_file(args.cover, args.image, args.out, image=True, config=config)
        else:
            print("error: provide one of --text/--text-file/--file/--image", file=sys.stderr)
            return 2
    except (PayloadError, CapacityError, FileNotFoundError, OSError) as exc:
        print(f"[hide] failed: {exc}", file=sys.stderr)
        return 1

    print("[hide] done")
    print(f"  preset            : {args.preset} (channel={config.channel}, "
          f"qstep={config.quant_step}, rs_nsym={config.rs_nsym})")
    print(f"  payload           : {result.payload_bytes} B")
    print(f"  after ANS         : {result.compressed_bytes} B")
    print(f"  after Reed-Solomon: {result.ecc_bytes} B")
    print(f"  STC width w       : {result.payload_width}")
    print(f"  coeffs changed    : {result.coeffs_changed}/{result.coeffs_total} "
          f"({result.change_rate:.2%})")
    print(f"  capacity          : {result.capacity_bytes} B (ANS+ECC)")
    print(f"  PSNR              : {result.psnr_db:.2f} dB")
    print(f"  output            : {result.out_path}")
    return 0


def _cmd_extract(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    try:
        result = extract(args.stego, config)
    except (PayloadError, FileNotFoundError, OSError) as exc:
        print(f"[extract] failed: {exc}", file=sys.stderr)
        return 1

    payload = result.payload
    print("[extract] done")
    print(f"  kind        : {payload.kind}")
    print(f"  STC width w : {result.payload_width}")
    print(f"  message     : {result.ecc_bytes} B (ANS+ECC)")

    if payload.ptype == PTYPE_TEXT:
        text = payload.as_text()
        print("  --- recovered text ---")
        print(text)
        if args.out_dir:
            os.makedirs(args.out_dir, exist_ok=True)
            path = os.path.join(args.out_dir, "message.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            print(f"  saved       : {path}")
    else:
        out_dir = args.out_dir or "."
        os.makedirs(out_dir, exist_ok=True)
        name = payload.name or "recovered.bin"
        path = os.path.join(out_dir, name)
        with open(path, "wb") as fh:
            fh.write(payload.data)
        print(f"  saved       : {path} ({len(payload.data)} B)")
    return 0


def _cmd_capacity(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    try:
        cap = capacity_bytes(args.cover, config)
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"[capacity] failed: {exc}", file=sys.stderr)
        return 1
    print(f"[capacity] {cap} B of ANS+ECC data fit in {args.cover} "
          f"(preset={args.preset})")
    return 0


def _cmd_channel(args: argparse.Namespace) -> int:
    from ans_stc.channel_simulator import evaluate, print_report
    from ans_stc.payload_manager import pack_text

    config = _config_from_args(args)
    container = pack_text(args.text or ("ANS-STC channel test. " * 6))
    rows = evaluate(args.cover, container, config)
    print_report(rows, title=f"Channel report (preset={args.preset})")
    return 0


def _cmd_watermark(args: argparse.Namespace) -> int:
    cfg = WatermarkConfig.strong() if args.strong else WatermarkConfig()
    try:
        if args.text is not None:
            result = wm_embed_text(args.cover, args.text, args.out, cfg)
        else:
            token = bytes.fromhex(args.token_hex)
            result = wm_embed(args.cover, token, args.out, cfg)
    except (WatermarkError, FileNotFoundError, OSError, ValueError) as exc:
        print(f"[watermark] failed: {exc}", file=sys.stderr)
        return 1

    print("[watermark] done")
    print(f"  payload   : {len(result.payload)} B (max {MAX_PAYLOAD_BYTES} B)")
    print(f"  strength  : {'strong' if args.strong else 'normal'}")
    print(f"  PSNR      : {result.psnr_db:.2f} dB")
    print(f"  output    : {result.out_path}")
    print("  survives  : Facebook / Instagram / WhatsApp resize + recompression")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    try:
        result = wm_extract(args.stego)
    except (FileNotFoundError, OSError) as exc:
        print(f"[verify] failed: {exc}", file=sys.stderr)
        return 1

    if not result.valid:
        print("[verify] no valid watermark found (unmarked image, wrong settings, or too degraded)")
        return 1
    print("[verify] watermark found and CRC-valid")
    print(f"  payload : {len(result.payload)} B")
    text = result.text
    if text:
        print(f"  as text : {text!r}")
    if len(result.payload) <= 64:
        print(f"  as hex  : {result.payload.hex()}")
    return 0


def _cmd_steganalysis(args: argparse.Namespace) -> int:
    from ans_stc import steganalysis as st
    from ans_stc.payload_manager import pack_text

    text = args.text or "authentic:mike-2026 provenance token"
    if args.scheme == "watermark":
        marker = st.watermark_marker(text.encode("utf-8"))
    else:
        marker = st.stego_marker(pack_text(text))

    def progress(done: int, total: int) -> None:
        print(f"\r[steganalysis] embedding + features {done}/{total}", end="", file=sys.stderr, flush=True)

    try:
        res = st.benchmark(args.scheme, marker, image_dir=args.images,
                           count=args.count, progress=progress)
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"\n[steganalysis] failed: {exc}", file=sys.stderr)
        return 1

    print("\n[steganalysis] done")
    print(f"  scheme     : {res.scheme}")
    print(f"  covers     : {res.source} ({res.n_pairs} pares)")
    print(f"  detector   : SPAM (686-D) + FLD, 5-fold cross-validation")
    print(f"  P_E        : {res.p_error:.3f}   (0.5 = indetectable, 0 = detectable)")
    print(f"  ROC AUC    : {res.auc:.3f}")
    print(f"  veredicto  : {res.verdict}")
    for note in res.notes:
        print(f"  nota       : {note}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ans-stc",
        description="Robust ANS-STC steganography — console core (Hito 1)",
    )
    parser.add_argument(
        "--preset", choices=list(_PRESETS), default="robust",
        help="embedding profile (default: robust — survives social-media JPEG)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_hide = sub.add_parser("hide", help="embed a payload into a cover image")
    p_hide.add_argument("--cover", required=True, help="carrier image (PNG/JPEG/...)")
    p_hide.add_argument("--out", required=True, help="output stego image (PNG recommended)")
    grp = p_hide.add_mutually_exclusive_group()
    grp.add_argument("--text", help="plain text to hide")
    grp.add_argument("--text-file", help="read text to hide from this UTF-8 file")
    grp.add_argument("--file", help="hide an arbitrary binary file (PDF, txt, ...)")
    grp.add_argument("--image", help="hide a small image file")
    p_hide.set_defaults(func=_cmd_hide)

    p_ext = sub.add_parser("extract", help="recover a payload from a stego image")
    p_ext.add_argument("--stego", required=True, help="suspicious/stego image")
    p_ext.add_argument("--out-dir", help="directory to save the recovered file/text")
    p_ext.set_defaults(func=_cmd_extract)

    p_cap = sub.add_parser("capacity", help="report how much data fits in a cover")
    p_cap.add_argument("--cover", required=True)
    p_cap.set_defaults(func=_cmd_capacity)

    p_chan = sub.add_parser("channel", help="JPEG robustness report for a cover")
    p_chan.add_argument("--cover", required=True)
    p_chan.add_argument("--text", help="text payload to probe with")
    p_chan.set_defaults(func=_cmd_channel)

    p_wm = sub.add_parser(
        "watermark",
        help="embed a resize-proof watermark up to 512 bytes (survives social-media re-compression)",
    )
    p_wm.add_argument("--cover", required=True, help="carrier image")
    p_wm.add_argument("--out", required=True, help="output marked image (PNG)")
    gwm = p_wm.add_mutually_exclusive_group(required=True)
    gwm.add_argument("--text", help="UTF-8 text to embed (up to 512 bytes)")
    gwm.add_argument("--token-hex", help="raw payload as hex (up to 1024 hex chars = 512 bytes)")
    p_wm.add_argument("--strong", action="store_true", help="extra robustness, ~3 dB lower PSNR")
    p_wm.set_defaults(func=_cmd_watermark)

    p_ver = sub.add_parser("verify", help="recover a watermark payload from an image")
    p_ver.add_argument("--stego", required=True, help="image to check (even after Facebook/WhatsApp)")
    p_ver.set_defaults(func=_cmd_verify)

    p_st = sub.add_parser("steganalysis", help="detectability benchmark (P_E / AUC) for a scheme")
    p_st.add_argument("--scheme", choices=["watermark", "stego"], default="watermark",
                      help="which embedding to analyse (default: watermark)")
    p_st.add_argument("--images", help="folder of cover images (else synthetic covers are used)")
    p_st.add_argument("--count", type=int, default=40, help="number of cover/stego pairs (default: 40)")
    p_st.add_argument("--text", help="payload text to embed during the benchmark")
    p_st.set_defaults(func=_cmd_steganalysis)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
