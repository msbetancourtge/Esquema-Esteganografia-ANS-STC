# ANS-STC · Robust Image Steganography

Hide a secret payload (text, a file, or a small image) **inside an image** so that
it is invisible to the eye and survives being re-compressed by social networks
(JPEG). Built for an anti-deepfake / cybersecurity course project.

The pipeline chains four ideas:

```
payload ──ANS──▶ compress ──Reed-Solomon──▶ protect ──STC / J-UNIWARD──▶ DCT coefficients ──▶ stego image
        (entropy coding)      (error correction)     (minimal-distortion embedding)
```

* **ANS** (Asymmetric Numeral Systems) compresses the payload.
* **Reed-Solomon** adds error-correction so the message survives channel noise.
* **STC + J-UNIWARD** embed the bits into mid-frequency DCT coefficients while
  changing the image as little (and as imperceptibly) as possible.

> **Two modes.** The pipeline above is **steganography** — it hides *kilobytes*
> and survives JPEG *re-compression*, but not social-media *resizing*. For images
> that will pass through **Facebook / WhatsApp / Pinterest**, use the second mode:
> a resize-proof **robust watermark** that embeds text (Brotli-compressed, up to
> **512 bytes compressed**) which
> survives downscaling + recompression (see [§8](#8-robust-watermark-mode--survives-facebook--whatsapp--pinterest)).

---

## 1. Setup (once)

Prerequisites: **Python 3.10+** (tested on 3.14).

```bash
# from the project folder
python3 -m venv .venv
source .venv/bin/activate        # macOS/Linux
# .venv\Scripts\activate         # Windows (PowerShell)

pip install -r requirements.txt
```

> **macOS note:** if the GUI fails with `ModuleNotFoundError: _tkinter`, install Tk
> support once with `brew install python-tk`.

After activating the environment you can call `python` directly. All commands
below assume the environment is active.

---

## 2. Three ways to run it

| Way | Command | Best for |
| --- | --- | --- |
| **GUI** (recommended) | `python app.py` | The **robust watermark** — Ocultar / Recuperar (see §8) |
| **Command line** | `python cli.py …` | Everything: steganography (hide/extract) **and** watermark/verify/steganalysis |
| **Standalone app** | `dist/ANS-STC.app` (macOS) · `dist/ANS-STC/ANS-STC` (Win/Linux) | Running with no Python installed (see §10) |

> The desktop app is now focused on the **resize-proof watermark** (§8). The
> high-capacity **steganography** mode (§3–§4) lives in the command line
> (`cli.py hide` / `cli.py extract`).

---

## 3. How to HIDE content (steganography, CLI)

### Using the GUI

1. Run `python app.py` and stay on the **Ocultar** (Hide) tab.
2. **① Imagen portadora** → *Cargar portadora*: pick the cover image (PNG or JPEG).
   The app shows a preview and the estimated capacity.
3. **② Carga útil** (payload) — choose one:
   * **Texto plano** — type your message in the box.
   * **Archivo** — *Elegir archivo…* to hide any file (PDF, txt, zip…).
   * **Imagen pequeña** — *Elegir archivo…* to hide a small image.
4. **③ Perfil de robustez** — pick a preset (see §6). *Robusto* is the default and
   best for images that will be shared on social media.
5. Click **Generar imagen segura** and choose where to save the result
   (**save as `.png`** — see the warning below).
6. The console shows the stats (payload size, PSNR, % of coefficients changed).

### Using the command line

```bash
# hide plain text
python cli.py hide --cover assets/sample_cover.png \
    --text "Reunión el viernes a las 8pm. Clave: colibrí-42." \
    --out outputs/demo_stego.png

# hide a whole file (PDF, txt, zip, …)
python cli.py hide --cover assets/sample_cover.png --file report.pdf --out outputs/stego.png

# hide a small image
python cli.py hide --cover assets/sample_cover.png --image logo.png --out outputs/stego.png

# read the text from a file instead of the command line
python cli.py hide --cover assets/sample_cover.png --text-file secret.txt --out outputs/stego.png
```

Example output:

```
[hide] done
  preset            : robust (channel=G, qstep=48, rs_nsym=120)
  payload           : 56 B
  after ANS         : 140 B
  after Reed-Solomon: 380 B
  coeffs changed    : 7081/129024 (5.49%)
  capacity          : 16064 B (ANS+ECC)
  PSNR              : 35.63 dB
  output            : outputs/demo_stego.png
```

> ⚠️ **Always save the stego image as PNG.** PNG is lossless, so extraction is
> guaranteed. If you save as JPEG yourself you re-compress it and may lose the
> message. (The scheme is *designed* to survive JPEG done later by a social
> network — see §7 — but you should hand it out as PNG.)

---

## 4. How to EXTRACT content (recover)

### Using the GUI

1. Switch to the **Extraer** (Extract) tab.
2. **① Imagen sospechosa** → *Cargar imagen*: load the stego image.
3. **② Perfil** — select the **same preset** that was used to hide. *(Both hide and
   extract default to Robusto, so if you did not change it, leave it.)*
4. Click **Extraer mensaje**.
5. The result appears in the **Resultado** panel:
   * hidden **text** is shown directly;
   * a hidden **file/image** prompts you for where to save it.

### Using the command line

```bash
# recover text (printed to the console; also saved if --out-dir is given)
python cli.py extract --stego outputs/demo_stego.png --out-dir outputs/recovered
```

Example output:

```
[extract] done
  kind        : text
  message     : 380 B (ANS+ECC)
  --- recovered text ---
Reunión el viernes a las 8pm. Clave: colibrí-42.
  saved       : outputs/recovered/message.txt
```

For a hidden file/image, the original filename is restored inside `--out-dir`.

> 🔑 **The preset used to extract must match the one used to hide.** Otherwise you
> will get `header sync mismatch`. Both sides default to `robust`.

---

## 5. What you can hide

| Payload type | GUI option | CLI flag |
| --- | --- | --- |
| Plain text | *Texto plano* | `--text "…"` or `--text-file file.txt` |
| Any file (PDF, txt, zip…) | *Archivo* | `--file path` |
| Small image | *Imagen pequeña* | `--image path` |

The original filename and type are stored with the payload and restored on extraction.

---

## 6. Robustness presets

| Preset (CLI / GUI) | Quant step | Reed-Solomon | Typical PSNR | Use when |
| --- | --- | --- | --- | --- |
| `max` / *Máxima calidad (PNG)* | 16 | 32 | ~46 dB | The stego stays PNG; you want the least visible change |
| `balanced` / *Balanceado* | 32 | 80 | ~40 dB | Light re-compression, high-quality JPEG (Q≥90) |
| `robust` / *Robusto* (default) | 48 | 120 | ~36 dB | The image will be shared on social media (JPEG) |

Set it on the command line **before** the sub-command:

```bash
python cli.py --preset balanced hide --cover cover.png --text "hi" --out out.png
python cli.py --preset balanced extract --stego out.png
```

Higher robustness ⇒ more error-correction and larger coefficient changes ⇒ lower
PSNR but better survival. Capacity shrinks accordingly.

---

## 7. Will it survive JPEG / social media?

Yes — that is the headline feature — but with realistic caveats:

* **PNG round-trip: always perfect.** If the stego image reaches the recipient as
  PNG, recovery is guaranteed.
* **JPEG re-compression:** the `robust` preset is tuned to survive social-media
  JPEG. Survival is highest at **quality ≥ 85** and depends on how textured the
  cover is (more texture = more resilient). Very aggressive compression
  (low quality + heavy chroma subsampling) can still defeat it.

Check a specific cover before trusting it:

```bash
python cli.py channel --cover assets/sample_cover.png
```

```
Channel report (preset=robust)
  Quality | coeff BER |  msg BER | recovered | note
  ------------------------------------------------------
       70 |   0.369% |  8.783% |       YES |
       80 |   0.430% |  9.145% |       YES |
       90 |   0.000% |  0.000% |       YES |
       95 |   0.000% |  0.000% |       YES |
```

`coeff BER` is the raw error rate the channel introduces; the STC layer amplifies
it into `msg BER`, which Reed-Solomon then repairs — the last column is what
matters. For a deeper sweep across presets:

```bash
python -m ans_stc.channel_simulator assets/sample_cover.png
```

---

## 8. Robust watermark mode — survives Facebook / WhatsApp / Pinterest

The steganography pipeline hides *kilobytes*, but — like every block-DCT scheme —
it dies when a platform **resizes** the image. Facebook, WhatsApp and Pinterest
all downscale **and** recompress every upload, which desynchronises the 8×8 grid
and yields `header sync mismatch` on extraction.

For that scenario there is a second, independent mode: a **resize-proof blind
watermark** that embeds a **variable-length text payload** (a short message, a
signature, or a hash) and survives the whole social-media gauntlet. Text is
Brotli-compressed first, so the 512-byte cap is on the *compressed* size —
ordinary prose can run well over 512 raw characters. It trades capacity for brute
robustness — ideal for an **anti-deepfake provenance mark** or a short
authenticated caption.

**Why it survives resizing** (the ideas):

1. **Canonical-resolution re-synchronisation** — both embedder and extractor
   normalise the image to a fixed 1152×1152 luminance grid, so whatever size a
   platform resamples to is undone before decoding.
2. **Improved Spread Spectrum (ISS)** — each bit is spread over dozens of
   mid-low DCT coefficients with the host image's own energy algebraically
   cancelled, giving huge processing gain (host-interference-free detection).
3. **Brotli entropy coding** — the text is compressed with Brotli (MODE_TEXT,
   which ships a built-in dictionary of common words), so low-entropy prose needs
   *far fewer bits*. Fewer bits mean more coefficients per bit (**more
   robustness**) and less watermark energy (**higher image quality**); a ~250-char
   paragraph drops ~45%. Short/incompressible strings fall back to raw storage.
4. **Reed-Solomon error correction** — a parity envelope repairs the residual
   bit errors a harsh channel still slips through.
5. **Bounded working resolution** — very large originals are downscaled to a
   2048-px longest side before embedding. This bounds the
   canonical→native→download resampling ratio that otherwise erodes the watermark
   band on high-resolution photos, and matches what platforms keep anyway.
6. **Perceptual masking** — the watermark is content-adaptive: it is concentrated
   in textured/edge regions (where the eye tolerates it) and eased out of smooth
   areas like sky or skin, so it stays visually clean.
7. **Adaptive per-payload strength** — a short message spreads over hundreds of
   coefficients per bit and is embedded quietly (high PSNR); a long message
   spreads over far fewer, so its amplitude ramps up automatically (~1/√coeffs
   per bit) to hold the same channel margin through a small download. Detection
   is sign-based, so the extractor never needs to know the amplitude used.
8. **Orientation resync** — extraction applies the image's EXIF orientation and,
   if the direct read fails, retries the 90/180/270° rotations, so phone photos
   and platforms that bake in rotation are handled automatically.

Container: a tiny ultra-redundant `MAGIC | LEN | NSYM | CRC-16 | FLAGS` header
plus a Reed-Solomon-protected, optionally Brotli-compressed payload region. The
header is decoded first and tells the extractor how many payload bits to read, so
**short messages use fewer bits → higher quality and more margin** (~38 dB), while
a full-capacity message still survives a small Facebook download (~31 dB). The
`FLAGS` byte reuses the old pad byte (0), so pre-compression marks still verify. A
wrong/absent watermark fails the header CRC or the RS decode and is reported as
"no watermark" instead of returning garbage.

### Using the GUI

* **Marca robusta** tab — load a cover, type your text (up to 512 bytes) or a hex
  payload, optionally tick *Máxima robustez*, and save the marked PNG.
* **Verificar** tab — load any image (even one just downloaded from Facebook) and
  read the message back.

### Using the command line

```bash
# embed text (up to 512 bytes)
python cli.py watermark --cover photo.jpg --text "authentic:mike-2026" --out marked.png

# embed a raw payload as hex (e.g. a SHA-256 hash), up to 1024 hex chars
python cli.py watermark --cover photo.jpg --token-hex 8f43434655aa... --out marked.png

# recover it — works even after the image passed through a social network
python cli.py verify --stego downloaded_from_facebook.jpg
```

Measured survival is **0 bit-errors / exact recovery of a 512-byte payload**
through faithful Facebook, WhatsApp, Pinterest and WhatsApp-status pipelines
(downscale to 2048 / 1600 / 736 / 1024 px + JPEG Q60–Q85, 4:2:0) on smooth,
textured, landscape and portrait covers — and still recovers from **small
Facebook feed downloads down to ~480 px**.

> **Limitation:** this resists *resize + recompression + 90° rotation*, but **not
> hard cropping** (a story/profile crop, or a **screenshot**, which removes
> content and moves the sync grid). Share the marked PNG **as a file / full
> image, without cropping**. True crop-invariance would need a Fourier–Mellin
> synchronisation template (future work).
>
> **Chained shares (e.g. WhatsApp → Facebook)** of the un-cropped image survive —
> the failure mode there is almost always a crop/screenshot somewhere in the
> chain, or an image marked by an older build (just re-mark it).

### Which mode should I use?

| | Steganography (Ocultar / Extraer) | Watermark (Marca / Verificar) |
| --- | --- | --- |
| Capacity | kilobytes (text, files, images) | text, Brotli-compressed to ≤512 B |
| Survives JPEG re-compression | ✅ | ✅ |
| Survives social-media **resize** | ❌ | ✅ |
| Best for | private payload delivery | anti-deepfake provenance mark |

---

## 9. Steganalysis: how detectable is it?

Robustness and undetectability are **opposing** goals — every dB spent surviving
social media is a dB of statistical footprint. This project ships a benchmark so
you can *measure* that trade-off (useful for a report).

It extracts **SPAM** features (686-D noise-residual co-occurrences; Pevný, Bas &
Fridrich 2010), trains a regularised **Fisher linear discriminant**, and reports
the steganalyst's error under 5-fold cross-validation:

```
P_E = min_t  ½ (P_FA(t) + P_MD(t))      P_E≈0.5 → undetectable · P_E≈0 → detectable
```

```bash
# analyse the watermark (use your own photos for a representative number)
python cli.py steganalysis --scheme watermark --images ./photos --count 60

# analyse the steganography mode
python cli.py steganalysis --scheme stego
```

Run it from the command line (it produces the numbers for a report; the GUI
itself stays focused on the watermark):

**Illustrative result (synthetic covers):**

| Scheme | P_E | ROC AUC | Reading |
| --- | --- | --- | --- |
| Watermark (ISS, ~35–40 dB) | ~0.00 | ~1.00 | fully detectable — *by design*; it is a watermark, not covert |
| Steganography (STC/J-UNIWARD) | ~0.20 | ~0.87 | detectable, but less so |

> These are a **relative yardstick** from a light detector — not a claim of
> security against a modern CNN steganalyzer (SRNet et al.), which would detect
> both more easily. The watermark is meant to be **robust and unforgeable**, not
> undetectable; the stego mode is **harder to detect but fragile**. Pick the axis
> your application needs.

---

## 10. Build the standalone executable (no Python needed)

```bash
pip install pyinstaller
pyinstaller ANS-STC.spec
```

Artifacts land in `dist/`:

* **macOS** — `dist/ANS-STC.app` (double-click) and `dist/ANS-STC/`
* **Windows** — `dist/ANS-STC/ANS-STC.exe`
* **Linux** — `dist/ANS-STC/ANS-STC`

PyInstaller does not cross-compile: run the same spec on each target OS to get
that platform's binary.

---

## 11. Quick reference

```bash
python app.py                                   # launch the GUI (watermark: Ocultar / Recuperar)
python cli.py hide --cover C --text "…" --out S # hide text (steganography)
python cli.py hide --cover C --file F --out S   # hide a file
python cli.py extract --stego S --out-dir D     # recover
python cli.py capacity --cover C                # how much fits
python cli.py channel  --cover C                # JPEG survival report
python cli.py watermark --cover C --text "…" --out M  # resize-proof watermark (<=512 B)
python cli.py verify    --stego M                     # read the mark back
python cli.py steganalysis --scheme watermark        # detectability P_E / AUC
python -m pytest -q                             # run the test suite (94 tests)
```

---

## 12. Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `header sync mismatch` | Wrong preset on extract, or the image was re-compressed/resized too hard. Use the same preset used to hide; prefer PNG. If it went through social media, use the **watermark** mode instead (§8). |
| `Reed-Solomon could not repair the payload` | Channel noise exceeded the ECC budget. Use the `robust` preset and/or a higher JPEG quality. |
| `payload does not fit` (CapacityError) | The message is larger than the cover allows. Use a bigger cover or run `capacity` first. |
| `verify`: no valid watermark found | The image is unmarked, or was cropped/rotated (watermark resists resize + recompression, not cropping). |
| GUI: `ModuleNotFoundError: _tkinter` (macOS) | `brew install python-tk`, then recreate the venv. |

---

## 13. Project layout

```
app.py                     Graphical app (customtkinter) — Ocultar / Recuperar
cli.py                     Command line (hide / extract / capacity / channel / watermark / verify / steganalysis)
ANS-STC.spec               PyInstaller build recipe
requirements.txt
ans_stc/
  payload_manager.py       ANS entropy coder + Reed-Solomon ECC
  transform_engine.py      8×8 block DCT + mid-frequency coefficient selection
  cost_calculator.py       J-UNIWARD distortion costs
  stc_core.py              Syndrome-Trellis Codes (Viterbi embed/extract)
  pipeline.py              End-to-end hide/extract orchestration
  robust_watermark.py      Resize-proof watermark up to 512 B (survives social media)
  channel_simulator.py     JPEG-robustness diagnostics
  steganalysis.py          Detectability benchmark (SPAM features + FLD, P_E / AUC)
  config.py                Shared parameters and presets
assets/sample_cover.png    A textured sample cover to experiment with
tests/                     Pytest suite (94 tests)
```
