"""RSW - Robust Spread-spectrum Watermark.

A resize-proof blind image watermark (Improved Spread Spectrum on a canonical
grid) that survives the downscale + JPEG recompression applied by social
networks.  A legacy minimal-distortion steganography pipeline - the project's
theoretical starting point, combining Asymmetric Numeral Systems (ANS) with
Syndrome-Trellis Codes (STC) - is retained as a secondary, high-capacity mode.

Package layout
--------------
- ``robust_watermark`` : the RSW watermark - ISS, canonical grid, perceptual mask.
- ``config``            : shared constants and the :class:`StegoConfig` dataclass.
- ``payload_manager``   : dynamic payload packing, rANS entropy coding, Reed-Solomon ECC.
- ``transform_engine``  : 8x8 block DCT, quantization and mid-frequency coefficient selection.
- ``cost_calculator``   : J-UNIWARD embedding-distortion costs.
- ``stc_core``          : Syndrome-Trellis Codes (Viterbi embedding + linear extraction).
- ``pipeline``          : end-to-end hide / extract orchestration (legacy stego mode).
- ``channel_simulator`` : JPEG channel model used to validate robustness.
"""

from .config import StegoConfig

__all__ = ["StegoConfig"]
__version__ = "1.0.0"
