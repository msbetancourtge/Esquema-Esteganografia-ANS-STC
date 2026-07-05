"""Steganalysis benchmark — how detectable is an embedding?

Implements a compact **SPAM-style** feature set (Pevny, Bas & Fridrich, 2010:
noise-residual co-occurrences modelled as Markov transition probabilities) plus a
regularised **Fisher Linear Discriminant** detector, evaluated with out-of-fold
cross-validation.  The headline number is the steganalyst's error probability

    P_E = min_t  1/2 (P_FA(t) + P_MD(t))        (equal priors)

where ``P_E ~ 0.5`` means *undetectable* (the detector is guessing) and ``P_E ~
0`` means *fully detectable*.  We also report ROC AUC.

This is a **relative** yardstick (a light detector on whatever covers you give
it), not a claim of absolute security against a modern CNN steganalyzer — use it
to compare schemes/strengths and to quantify the robustness/undetectability
trade-off for a report.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np
from PIL import Image
from scipy.stats import rankdata

_T = 3                      # residual truncation -> alphabet {-3..3}
_Q = 2 * _T + 1
_DIM = _Q ** 3             # transition tensor size per direction group


# --------------------------------------------------------------------------- #
# SPAM-style features
# --------------------------------------------------------------------------- #


def _transition(residual: np.ndarray) -> np.ndarray:
    """2nd-order Markov transition tensor of a truncated residual (row-wise)."""
    d = np.clip(residual, -_T, _T).astype(np.int64) + _T
    d0 = d[:, :-2].ravel()
    d1 = d[:, 1:-1].ravel()
    d2 = d[:, 2:].ravel()
    triples = np.bincount((d0 * _Q + d1) * _Q + d2, minlength=_DIM).astype(np.float64)
    pairs = np.bincount(d0 * _Q + d1, minlength=_Q * _Q).astype(np.float64)
    trans = triples.reshape(_Q, _Q, _Q)
    denom = pairs.reshape(_Q, _Q)[:, :, None]
    trans /= np.where(denom > 0, denom, 1.0)      # conditional P(d2 | d0, d1)
    return trans.ravel()


def spam_features(gray: np.ndarray) -> np.ndarray:
    """686-D SPAM feature vector of a grayscale image.

    Horizontal+vertical residuals form one group, the two diagonals another;
    each group is averaged over its directions (exploiting image symmetry).
    """
    x = gray.astype(np.int16)
    # residuals along 4 directions
    rh = x[:, :-1] - x[:, 1:]                       # horizontal
    rv = (x[:-1, :] - x[1:, :]).T                   # vertical (transpose -> row-wise)
    rd = x[:-1, :-1] - x[1:, 1:]                     # main diagonal
    rm = x[:-1, 1:] - x[1:, :-1]                     # minor diagonal
    f1 = 0.5 * (_transition(rh) + _transition(rv))
    f2 = 0.5 * (_transition(rd) + _transition(rm))
    return np.concatenate([f1, f2])


def _luma(path: str) -> np.ndarray:
    return np.asarray(Image.open(path).convert("L"), dtype=np.int16)


# --------------------------------------------------------------------------- #
# Detector: regularised FLD + cross-validated P_E / AUC
# --------------------------------------------------------------------------- #


def _pe_and_roc(scores: np.ndarray, labels: np.ndarray):
    """Minimal error probability (equal priors) and ROC AUC from scores.

    ``labels``: 1 = stego, 0 = cover.  Higher score should mean "more stego".
    """
    order = np.argsort(scores)
    s = scores[order]
    y = labels[order]
    n_pos = float(y.sum())          # stego
    n_neg = float(len(y) - n_pos)   # cover
    # sweep threshold: predict stego if score > t
    # as t increases past each point, that sample flips cover<-
    tp = n_pos - np.cumsum(y)                      # stego above threshold
    fp = n_neg - np.cumsum(1 - y)                  # cover above threshold (false alarm)
    p_fa = fp / max(n_neg, 1.0)
    p_md = 1.0 - tp / max(n_pos, 1.0)
    pe = 0.5 * (p_fa + p_md)
    best_pe = float(pe.min())
    # ROC AUC via Mann-Whitney U with average ranks (correct under ties)
    ranks = rankdata(scores)
    auc = (ranks[labels == 1].sum() - n_pos * (n_pos + 1) / 2) / max(n_pos * n_neg, 1.0)
    return best_pe, float(auc)


def evaluate_detector(cover_feats: np.ndarray, stego_feats: np.ndarray,
                      folds: int = 5, ridge: float = 1e-2, seed: int = 0):
    """Cross-validated FLD steganalysis. Returns ``(P_E, AUC)`` (out-of-fold)."""
    rng = np.random.default_rng(seed)
    n = min(len(cover_feats), len(stego_feats))
    cover_feats, stego_feats = cover_feats[:n], stego_feats[:n]
    ci = rng.permutation(n)
    si = rng.permutation(n)
    all_scores = np.zeros(2 * n)
    all_labels = np.concatenate([np.zeros(n), np.ones(n)])
    for k in range(folds):
        test_c = ci[k::folds]
        test_s = si[k::folds]
        tr_c = np.setdiff1d(ci, test_c)
        tr_s = np.setdiff1d(si, test_s)
        Xtr = np.vstack([cover_feats[tr_c], stego_feats[tr_s]])
        mu = Xtr.mean(0)
        sd = Xtr.std(0) + 1e-9
        Xc = (cover_feats[tr_c] - mu) / sd
        Xs = (stego_feats[tr_s] - mu) / sd
        within = np.cov(Xc, rowvar=False) + np.cov(Xs, rowvar=False)
        w = np.linalg.solve(within + ridge * np.eye(within.shape[0]), Xs.mean(0) - Xc.mean(0))
        for idx, feats in ((test_c, cover_feats), (test_s, stego_feats)):
            proj = ((feats[idx] - mu) / sd) @ w
            base = 0 if feats is cover_feats else n
            all_scores[base + idx] = proj
    return _pe_and_roc(all_scores, all_labels)


# --------------------------------------------------------------------------- #
# Synthetic cover fallback (labelled; real photos are strongly preferred)
# --------------------------------------------------------------------------- #


def synthetic_covers(count: int, size: int = 512, seed: int = 0) -> list[np.ndarray]:
    """Diverse pseudo-natural textured grayscale covers (fallback only)."""
    rng = np.random.default_rng(seed)
    covers = []
    yy, xx = np.mgrid[0:size, 0:size]
    for _ in range(count):
        f1, f2, f3 = rng.uniform(8, 45, 3)
        amp = rng.uniform(20, 60)
        base = (128 + amp * np.sin(xx / f1) + amp * 0.7 * np.cos(yy / f2)
                + amp * 0.5 * np.sin((xx + yy) / f3))
        noise = rng.normal(0, rng.uniform(3, 14), (size, size))
        covers.append(np.clip(base + noise, 0, 255).astype(np.uint8))
    return covers


# --------------------------------------------------------------------------- #
# Benchmark orchestration
# --------------------------------------------------------------------------- #


@dataclass
class SteganalysisResult:
    scheme: str
    n_pairs: int
    p_error: float
    auc: float
    source: str
    notes: list[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if self.p_error >= 0.40:
            return "difícil de detectar (P_E alto)"
        if self.p_error >= 0.25:
            return "moderadamente detectable"
        return "fácilmente detectable (P_E bajo)"


def gather_cover_grays(image_dir: str | None, count: int, seed: int = 0):
    """Load up to ``count`` grayscale covers from a folder, else synthesise them."""
    if image_dir and os.path.isdir(image_dir):
        exts = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp")
        paths = sorted(
            os.path.join(image_dir, f) for f in os.listdir(image_dir)
            if f.lower().endswith(exts)
        )[:count]
        if paths:
            return [_luma(p) for p in paths], f"{len(paths)} imágenes de {image_dir}"
    return synthetic_covers(count, seed=seed), f"{count} portadoras sintéticas (ilustrativo)"


def benchmark(scheme: str, mark, image_dir: str | None = None,
              count: int = 40, seed: int = 0, progress=None) -> SteganalysisResult:
    """Run the full benchmark.

    ``mark(cover_gray_uint8) -> (clean_gray, marked_gray)`` returns the *clean*
    and *marked* versions of the channel that the scheme modifies, at the same
    resolution — so the detector sees only the embedding as the difference.
    """
    covers, source = gather_cover_grays(image_dir, count, seed)
    cover_feats, stego_feats = [], []
    for i, cov in enumerate(covers):
        if progress:
            progress(i + 1, len(covers))
        clean, marked = mark(np.clip(cov, 0, 255).astype(np.uint8))
        cover_feats.append(spam_features(clean))
        stego_feats.append(spam_features(marked))
    cover_feats = np.asarray(cover_feats)
    stego_feats = np.asarray(stego_feats)
    p_e, auc = evaluate_detector(cover_feats, stego_feats, seed=seed)
    notes = []
    if source.endswith("(ilustrativo)"):
        notes.append("Usa imágenes reales para un resultado representativo.")
    return SteganalysisResult(scheme, len(covers), p_e, auc, source, notes)


# --------------------------------------------------------------------------- #
# Scheme adapters: turn an embedding into a mark(cover) -> (clean, marked)
# --------------------------------------------------------------------------- #


def watermark_marker(payload: bytes, config=None):
    """Marker for the robust watermark (analyses the luminance channel)."""
    import tempfile

    from .robust_watermark import embed

    tmp = tempfile.mkdtemp(prefix="stegan_wm_")
    cin = os.path.join(tmp, "c.png")
    cout = os.path.join(tmp, "m.png")

    def mark(gray: np.ndarray):
        Image.fromarray(np.stack([gray] * 3, -1), "RGB").save(cin)
        embed(cin, payload, cout, config)
        marked_img = Image.open(cout).convert("RGB")
        marked = np.asarray(marked_img.convert("L"), dtype=np.int16)
        clean = np.asarray(
            Image.fromarray(gray, "L").resize(marked_img.size, Image.LANCZOS),
            dtype=np.int16,
        )
        return clean, marked

    return mark


def stego_marker(container: bytes, config=None):
    """Marker for the STC/J-UNIWARD steganography (analyses the embedded plane)."""
    import tempfile

    from .pipeline import hide

    tmp = tempfile.mkdtemp(prefix="stegan_st_")
    cin = os.path.join(tmp, "c.png")
    cout = os.path.join(tmp, "m.png")
    channel = {"R": 0, "G": 1, "B": 2}.get(getattr(config, "channel", "G"), 1) if config else 1

    def mark(gray: np.ndarray):
        Image.fromarray(np.stack([gray] * 3, -1), "RGB").save(cin)
        hide(cin, container, cout, config)
        marked = np.asarray(Image.open(cout).convert("RGB"), dtype=np.int16)[:, :, channel]
        clean = gray.astype(np.int16)
        return clean, marked

    return mark

