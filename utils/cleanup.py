import os
import shutil
import time
import logging
from utils.config import TEMP_DIR, OUTPUT_DIR, TEMP_FILE_MAX_AGE_HOURS

logger = logging.getLogger(__name__)


def delete_path(path: str) -> None:
    """Delete a file or directory tree, logging errors but not raising."""
    try:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.isfile(path):
            os.remove(path)
    except Exception as e:
        logger.warning(f"Cleanup failed for {path}: {e}")


def cleanup_old_files() -> None:
    """Delete files in temp/ and output/ older than TEMP_FILE_MAX_AGE_HOURS."""
    cutoff = time.time() - TEMP_FILE_MAX_AGE_HOURS * 3600
    for directory in (TEMP_DIR, OUTPUT_DIR):
        if not os.path.isdir(directory):
            continue
        for name in os.listdir(directory):
            full = os.path.join(directory, name)
            try:
                if os.path.getmtime(full) < cutoff:
                    delete_path(full)
                    logger.info(f"Auto-cleaned old file: {full}")
            except Exception as e:
                logger.warning(f"Could not check mtime for {full}: {e}")
