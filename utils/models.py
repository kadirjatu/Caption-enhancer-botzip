import os
import sys
import logging
from utils.config import REALESRGAN_DIR, MODELS_DIR, REALESRGAN_MODELS
from utils.download import ensure_realesrgan_repo, ensure_model
from utils.gpu import get_backend

logger = logging.getLogger(__name__)

_upsampler_cache: dict = {}


def _get_python_upsampler(scale: int = 4):
    """
    Load and return a RealESRGANer instance (Python backend).
    Results are cached by scale to avoid reloading weights.
    """
    if scale in _upsampler_cache:
        return _upsampler_cache[scale]

    ensure_realesrgan_repo()

    if REALESRGAN_DIR not in sys.path:
        sys.path.insert(0, REALESRGAN_DIR)

    try:
        from basicsr.archs.rrdbnet_arch import RRDBNet
        from realesrgan import RealESRGANer
    except ImportError as e:
        raise RuntimeError(
            f"Real-ESRGAN Python dependencies not installed: {e}\n"
            "Run: pip install basicsr realesrgan"
        )

    if scale == 2:
        model_name = REALESRGAN_MODELS["x2plus"]
        from basicsr.archs.rrdbnet_arch import RRDBNet
        model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64,
                        num_block=23, num_grow_ch=32, scale=2)
    else:
        model_name = REALESRGAN_MODELS["x4plus"]
        model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64,
                        num_block=23, num_grow_ch=32, scale=4)

    model_path = ensure_model(model_name)

    upsampler = RealESRGANer(
        scale=scale,
        model_path=model_path,
        model=model,
        tile=0,
        tile_pad=10,
        pre_pad=0,
        half=False,
    )
    _upsampler_cache[scale] = upsampler
    logger.info(f"Loaded RealESRGANer (scale={scale}, model={model_name})")
    return upsampler


def upscale_image_array(img_array, scale: int = 4):
    """
    Upscale a numpy BGR image array using Real-ESRGAN Python backend.
    Returns the enhanced numpy array.
    """
    upsampler = _get_python_upsampler(scale)
    output, _ = upsampler.enhance(img_array, outscale=scale)
    return output
