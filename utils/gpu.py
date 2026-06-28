import shutil
import subprocess
import logging

logger = logging.getLogger(__name__)


def has_vulkan_gpu() -> bool:
    """Return True if a Vulkan-compatible GPU is detected."""
    if shutil.which("realesrgan-ncnn-vulkan"):
        try:
            result = subprocess.run(
                ["realesrgan-ncnn-vulkan", "-h"],
                capture_output=True, timeout=10
            )
            return True
        except Exception:
            pass
    try:
        result = subprocess.run(
            ["vulkaninfo", "--summary"],
            capture_output=True, timeout=10
        )
        if result.returncode == 0 and b"GPU" in result.stdout:
            return True
    except FileNotFoundError:
        pass
    return False


def get_backend() -> str:
    """
    Return 'ncnn' if Vulkan GPU + binary available,
    otherwise 'python' for the official Python Real-ESRGAN implementation.
    """
    if has_vulkan_gpu():
        logger.info("GPU backend: realesrgan-ncnn-vulkan")
        return "ncnn"
    logger.info("GPU backend: Python Real-ESRGAN (CPU)")
    return "python"
