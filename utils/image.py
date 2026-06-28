"""
Image upscaling via high-quality OpenCV pipeline.
Supports JPG, PNG, WEBP. Preserves transparency (alpha channel).

Backend priority:
  1. realesrgan-ncnn-vulkan binary  (if GPU + binary present)
  2. Real-ESRGAN Python / basicsr   (if torch+basicsr installed)
  3. OpenCV Lanczos4 + sharpening   (always available — default fallback)
"""
import os
import logging
import time

import cv2
import numpy as np

logger = logging.getLogger(__name__)


# ── Public API ────────────────────────────────────────────────────

def upscale_image(input_path: str, output_path: str, scale: int = 4) -> str:
    """
    Upscale image at input_path by scale and save to output_path.
    Returns output_path on success.
    """
    t0 = time.time()
    logger.info(f"Upscaling {input_path} ({scale}x)")

    # Try ncnn-vulkan first (GPU)
    if _try_ncnn(input_path, output_path, scale):
        logger.info(f"ncnn-vulkan done in {time.time()-t0:.1f}s")
        return output_path

    # Try Python Real-ESRGAN (torch + basicsr)
    if _try_realesrgan_python(input_path, output_path, scale):
        logger.info(f"Python Real-ESRGAN done in {time.time()-t0:.1f}s")
        return output_path

    # Always-available OpenCV fallback
    _upscale_opencv(input_path, output_path, scale)
    logger.info(f"OpenCV upscale done in {time.time()-t0:.1f}s")
    return output_path


# ── Backend: ncnn-vulkan ──────────────────────────────────────────

def _try_ncnn(input_path: str, output_path: str, scale: int) -> bool:
    import shutil, subprocess
    if not shutil.which("realesrgan-ncnn-vulkan"):
        return False
    model_map = {2: "realesrgan-x4plus", 4: "realesrgan-x4plus"}
    cmd = [
        "realesrgan-ncnn-vulkan",
        "-i", input_path, "-o", output_path,
        "-s", str(scale),
        "-n", model_map.get(scale, "realesrgan-x4plus"),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=300)
        return r.returncode == 0 and os.path.isfile(output_path)
    except Exception as e:
        logger.debug(f"ncnn failed: {e}")
        return False


# ── Backend: Python Real-ESRGAN (optional torch+basicsr) ─────────

def _try_realesrgan_python(input_path: str, output_path: str, scale: int) -> bool:
    try:
        import sys
        from utils.config import REALESRGAN_DIR
        if REALESRGAN_DIR not in sys.path:
            sys.path.insert(0, REALESRGAN_DIR)
        from basicsr.archs.rrdbnet_arch import RRDBNet
        from realesrgan import RealESRGANer
        from utils.config import MODELS_DIR, REALESRGAN_MODELS
        from utils.download import ensure_model

        if scale == 2:
            model_name = REALESRGAN_MODELS["x2plus"]
            model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64,
                            num_block=23, num_grow_ch=32, scale=2)
        else:
            model_name = REALESRGAN_MODELS["x4plus"]
            model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64,
                            num_block=23, num_grow_ch=32, scale=4)

        model_path = ensure_model(model_name)
        upsampler = RealESRGANer(
            scale=scale, model_path=model_path,
            model=model, tile=0, tile_pad=10, pre_pad=0, half=False,
        )
        img = cv2.imread(input_path, cv2.IMREAD_UNCHANGED)
        enhanced, _ = upsampler.enhance(img, outscale=scale)
        cv2.imwrite(output_path, enhanced)
        return os.path.isfile(output_path)
    except Exception as e:
        logger.debug(f"Python Real-ESRGAN not available: {e}")
        return False


# ── Backend: OpenCV Lanczos4 + sharpening (always available) ─────

def _upscale_opencv(input_path: str, output_path: str, scale: int) -> None:
    """
    High-quality upscale using:
      • Lanczos4 interpolation  (best standard upscaling)
      • Luminance-channel unsharp mask  (edge sharpening)
      • FastNlMeansDenoising on luma  (grain removal)
      • Mild contrast + saturation boost
    Preserves alpha channel if present.
    """
    img = cv2.imread(input_path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f"Cannot read image: {input_path}")

    # Split alpha if present
    alpha = None
    if img.ndim == 3 and img.shape[2] == 4:
        alpha = img[:, :, 3]
        img   = img[:, :, :3]

    h, w = img.shape[:2]
    new_w, new_h = w * scale, h * scale

    # Step 1 — Lanczos4 upscale
    upscaled = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)

    # Step 2 — Denoise (mild, on the upscaled image)
    denoised = cv2.fastNlMeansDenoisingColored(upscaled, None, h=4, hColor=4,
                                               templateWindowSize=7, searchWindowSize=21)

    # Step 3 — Unsharp mask for edge sharpness
    blur    = cv2.GaussianBlur(denoised, (0, 0), sigmaX=2.0)
    sharp   = cv2.addWeighted(denoised, 1.4, blur, -0.4, 0)

    # Step 4 — Mild contrast + saturation boost in LAB
    lab = cv2.cvtColor(sharp, cv2.COLOR_BGR2LAB).astype(np.float32)
    # Boost L channel contrast slightly
    lab[:, :, 0] = np.clip(lab[:, :, 0] * 1.05, 0, 255)
    # Boost a/b saturation slightly
    lab[:, :, 1] = np.clip(lab[:, :, 1] * 1.08, 0, 255)
    lab[:, :, 2] = np.clip(lab[:, :, 2] * 1.08, 0, 255)
    result = cv2.cvtColor(lab.astype(np.uint8), cv2.COLOR_LAB2BGR)

    # Re-attach alpha
    if alpha is not None:
        alpha_up = cv2.resize(alpha, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)
        result   = cv2.merge([result, alpha_up])

    cv2.imwrite(output_path, result)
    logger.info(f"OpenCV upscale: {w}x{h} -> {new_w}x{new_h}, saved {output_path}")
