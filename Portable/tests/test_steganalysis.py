"""Tests for the steganalysis benchmark."""

from __future__ import annotations

import numpy as np

from rsw import steganalysis as st
from rsw.payload_manager import pack_text
from rsw.robust_watermark import WatermarkConfig


def test_spam_feature_dimension():
    gray = np.random.default_rng(0).integers(0, 256, (200, 200), dtype=np.uint8)
    assert st.spam_features(gray).shape == (686,)


def test_identical_is_undetectable():
    covers = st.synthetic_covers(20, size=192, seed=1)
    feats = np.array([st.spam_features(c) for c in covers])
    p_e, auc = st.evaluate_detector(feats, feats.copy())
    assert p_e == 0.5            # no difference -> detector cannot separate
    assert 0.0 <= auc <= 1.0


def test_strong_signal_is_detectable():
    rng = np.random.default_rng(2)
    covers = st.synthetic_covers(20, size=192, seed=2)
    cover_feats = np.array([st.spam_features(c) for c in covers])
    # add a strong structured perturbation -> should be easy to detect
    stego_feats = np.array([
        st.spam_features(np.clip(c.astype(int) + 12 * ((np.indices(c.shape).sum(0)) % 2), 0, 255).astype(np.uint8))
        for c in covers
    ])
    p_e, auc = st.evaluate_detector(cover_feats, stego_feats)
    assert p_e < 0.2
    assert auc > 0.8


def test_watermark_benchmark_runs():
    res = st.benchmark("watermark", st.watermark_marker(b"token-de-prueba"), count=8, seed=3)
    assert res.n_pairs == 8
    assert 0.0 <= res.p_error <= 0.5
    assert 0.0 <= res.auc <= 1.0
    assert isinstance(res.verdict, str) and res.verdict


def test_stego_benchmark_runs():
    res = st.benchmark("stego", st.stego_marker(pack_text("hola")), count=8, seed=3)
    assert res.n_pairs == 8
    assert 0.0 <= res.p_error <= 0.5


def test_result_verdict_thresholds():
    r_hi = st.SteganalysisResult("x", 10, 0.45, 0.55, "src")
    r_mid = st.SteganalysisResult("x", 10, 0.30, 0.7, "src")
    r_lo = st.SteganalysisResult("x", 10, 0.05, 0.99, "src")
    assert "difícil" in r_hi.verdict
    assert "moderadamente" in r_mid.verdict
    assert "detectable" in r_lo.verdict
