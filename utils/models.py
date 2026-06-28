"""
Model management for Real-ESRGAN.
Tries Python Real-ESRGAN (requires basicsr+torch) first;
falls back gracefully to the OpenCV pipeline in utils/image.py.
"""
import logging

logger = logging.getLogger(__name__)


def upscale_image_array(img_array, scale: int = 4):
    """
    Upscale a numpy BGR array using Python Real-ESRGAN (if available).
    Raises ImportError if basicsr/torch not installed — caller should fall back.
    """
    import sys
    from utils.config import REALESRGAN_DIR, REALESRGAN_MODELS
    from utils.download import ensure_model

    if REALESRGAN_DIR not in sys.path:
        sys.path.insert(0, REALESRGAN_DIR)

    from basicsr.archs.rrdbnet_arch import RRDBNet   # raises ImportError if missing
    from realesrgan import RealESRGANer

    if scale == 2:
        model_name = REALESRGAN_MODELS["x2plus"]
        model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64,
                        num_block=23, num_grow_ch=32, scale=2)
    else:
        model_name = REALESRGAN_MODELS["x4plus"]
        model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64,
                        num_block=23, num_grow_ch=32, scale=4)

    model_path = ensure_model(model_name)
    upsampler  = RealESRGANer(
        scale=scale, model_path=model_path, model=model,
        tile=0, tile_pad=10, pre_pad=0, half=False,
    )
    output, _ = upsampler.enhance(img_array, outscale=scale)
    return output
