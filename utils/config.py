import os

# ─── Directories ──────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_DIR  = os.path.join(BASE_DIR, "models")
TEMP_DIR    = os.path.join(BASE_DIR, "temp")
OUTPUT_DIR  = os.path.join(BASE_DIR, "output")

for _d in (MODELS_DIR, TEMP_DIR, OUTPUT_DIR):
    os.makedirs(_d, exist_ok=True)

# ─── Real-ESRGAN ──────────────────────────────────────────
REALESRGAN_REPO     = "https://github.com/xinntao/Real-ESRGAN.git"
REALESRGAN_DIR      = os.path.join(BASE_DIR, "Real-ESRGAN")
REALESRGAN_NCNN_BIN = "realesrgan-ncnn-vulkan"

REALESRGAN_MODELS = {
    "x4plus":       "RealESRGAN_x4plus.pth",
    "x4plus_anime": "RealESRGAN_x4plus_anime_6B.pth",
    "x2plus":       "RealESRGAN_x2plus.pth",
}

REALESRGAN_MODEL_URLS = {
    "RealESRGAN_x4plus.pth":           "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
    "RealESRGAN_x4plus_anime_6B.pth":  "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth",
    "RealESRGAN_x2plus.pth":           "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth",
}

# ─── Video ────────────────────────────────────────────────
MAX_VIDEO_DURATION_SEC = 300   # 5 minutes
SUPPORTED_VIDEO_EXTS   = {".mp4", ".mov", ".mkv", ".avi"}
SUPPORTED_IMAGE_EXTS   = {".jpg", ".jpeg", ".png", ".webp"}

# ─── Cleanup ──────────────────────────────────────────────
TEMP_FILE_MAX_AGE_HOURS = 24

# ─── Telegram ─────────────────────────────────────────────
TELEGRAM_MAX_BYTES = 49 * 1024 * 1024   # 49 MB safe limit
