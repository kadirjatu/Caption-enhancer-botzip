import os
import subprocess
import logging
import requests
from utils.config import (
    MODELS_DIR, REALESRGAN_DIR, REALESRGAN_REPO,
    REALESRGAN_MODEL_URLS
)

logger = logging.getLogger(__name__)


def download_file(url: str, dest_path: str) -> None:
    """Download a file from url to dest_path with progress logging."""
    logger.info(f"Downloading {url} -> {dest_path}")
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    logger.debug(f"  {pct}% ({downloaded}/{total})")
    logger.info(f"Download complete: {dest_path}")


def ensure_realesrgan_repo() -> None:
    """Clone Real-ESRGAN repo if not already present."""
    if os.path.isdir(REALESRGAN_DIR):
        logger.info(f"Real-ESRGAN repo already at {REALESRGAN_DIR}")
        return
    logger.info("Cloning Real-ESRGAN repository...")
    result = subprocess.run(
        ["git", "clone", "--depth", "1", REALESRGAN_REPO, REALESRGAN_DIR],
        capture_output=True, timeout=120
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to clone Real-ESRGAN:\n{result.stderr.decode(errors='ignore')}"
        )
    logger.info("Real-ESRGAN cloned successfully.")


def ensure_model(model_filename: str) -> str:
    """
    Ensure a pretrained model file exists in models/.
    Downloads it automatically if missing.
    Returns the full path to the model file.
    """
    dest = os.path.join(MODELS_DIR, model_filename)
    if os.path.isfile(dest):
        logger.info(f"Model already cached: {dest}")
        return dest
    url = REALESRGAN_MODEL_URLS.get(model_filename)
    if not url:
        raise ValueError(f"Unknown model: {model_filename}. No download URL found.")
    download_file(url, dest)
    return dest
