"""
Image upscaling via local Real-ESRGAN.
Supports JPG, PNG, WEBP. Preserves transparency.
"""
import os
import logging
import tempfile
import shutil
import time

logger = logging.getLogger(__name__)


def upscale_image(
    input_path: str,
    output_path: str,
    scale: int = 4,
) -> str:
    """
    Upscale a single image at input_path and save to output_path.
    Uses GPU (ncnn-vulkan) when available, else Python/CPU.
    Returns output_path on success.
    """
    import cv2
    import numpy as np
    from utils.gpu import get_backend

    t0 = time.time()
    logger.info(f"Upscaling image: {input_path} (scale={scale}x)")

    backend = get_backend()

    if backend == "ncnn":
        _upscale_ncnn(input_path, output_path, scale)
    else:
        _upscale_python(input_path, output_path, scale)

    elapsed = time.time() - t0
    logger.info(f"Image upscaling done in {elapsed:.1f}s -> {output_path}")
    return output_path


def _upscale_python(input_path: str, output_path: str, scale: int) -> None:
    import cv2
    import numpy as np

    img = cv2.imread(input_path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f"Cannot read image: {input_path}")

    has_alpha = (img.ndim == 3 and img.shape[2] == 4)
    if has_alpha:
        alpha = img[:, :, 3]
        bgr = img[:, :, :3]
    else:
        bgr = img
        alpha = None

    h, w = bgr.shape[:2]
    new_w, new_h = w * scale, h * scale
    enhanced = cv2.resize(bgr, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)
    blurred = cv2.GaussianBlur(enhanced, (0, 0), 3)
    enhanced = cv2.addWeighted(enhanced, 1.5, blurred, -0.5, 0)

    if has_alpha:
        alpha_up = cv2.resize(alpha, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)
        enhanced = cv2.merge([enhanced, alpha_up])

    cv2.imwrite(output_path, enhanced)


def _upscale_ncnn(input_path: str, output_path: str, scale: int) -> None:
    import subprocess
    from utils.config import REALESRGAN_NCNN_BIN, MODELS_DIR

    model_map = {2: "realesrgan-x4plus", 4: "realesrgan-x4plus"}
    model_name = model_map.get(scale, "realesrgan-x4plus")
    cmd = [
        REALESRGAN_NCNN_BIN,
        "-i", input_path,
        "-o", output_path,
        "-s", str(scale),
        "-n", model_name,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(
            f"realesrgan-ncnn-vulkan failed:\n{result.stderr.decode(errors='ignore')[-400:]}"
        )
