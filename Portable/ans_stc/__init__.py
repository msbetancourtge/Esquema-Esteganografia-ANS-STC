"""ANS-STC: Robust steganography with Asymmetric Numeral Systems + Syndrome-Trellis Codes.

Package layout
--------------
- ``config``            : shared constants and the :class:`StegoConfig` dataclass.
- ``payload_manager``   : dynamic payload packing, rANS entropy coding, Reed-Solomon ECC.
- ``transform_engine``  : 8x8 block DCT, quantization and mid-frequency coefficient selection.
- ``cost_calculator``   : J-UNIWARD embedding-distortion costs.
- ``stc_core``          : Syndrome-Trellis Codes (Viterbi embedding + linear extraction).
- ``pipeline``          : end-to-end hide / extract orchestration.
- ``channel_simulator`` : JPEG channel model used to validate robustness.
"""

from .config import StegoConfig

__all__ = ["StegoConfig"]
__version__ = "1.0.0"
