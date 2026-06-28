"""
Local Real-ESRGAN upscaling — command handlers + processing functions.
Photo/video routing is handled via minimal hooks in the existing bot.py handlers.
Call register_upscale_handlers(bot) once from bot.py.
"""
import os
import logging
import tempfile
import threading
import time
import requests as _requests

from utils.config import TEMP_DIR, OUTPUT_DIR, TELEGRAM_MAX_BYTES
from utils.cleanup import delete_path, cleanup_old_files
from utils.progress import send_progress, send_new_status

logger = logging.getLogger(__name__)

# Shared state — imported by bot.py to check inside existing photo/video handlers
pending_local_enhance: dict = {}   # str(chat_id) -> {"scale": int, "type": "image"|"video"}


def _get_bot_token() -> str:
    return os.getenv("BOT_TOKEN", "")


def _download_telegram_file(bot, file_id: str, dest_path: str) -> None:
    """Download a Telegram file to dest_path."""
    file_info = bot.get_file(file_id)
    url = f"https://api.telegram.org/file/bot{_get_bot_token()}/{file_info.file_path}"
    resp = _requests.get(url, stream=True, timeout=180)
    resp.raise_for_status()
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)


def _compress_if_needed(path: str) -> str:
    """Compress video if > 49 MB using FFmpeg. Returns final path."""
    if os.path.getsize(path) <= TELEGRAM_MAX_BYTES:
        return path
    import subprocess
    base, _ = os.path.splitext(path)
    out = base + "_compressed.mp4"
    for crf in (28, 32, 36, 40):
        subprocess.run([
            "ffmpeg", "-y", "-i", path,
            "-c:v", "libx264", "-preset", "fast", "-crf", str(crf),
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
            out
        ], capture_output=True)
        if os.path.isfile(out) and os.path.getsize(out) <= TELEGRAM_MAX_BYTES:
            return out
    return out


# ── Image processing job ──────────────────────────────────────────

def process_local_image(bot, message, file_id: str, scale: int, ext: str = ".jpg") -> None:
    """Download image, upscale with local Real-ESRGAN, send result."""
    chat_id = message.chat.id
    status  = send_new_status(
        bot, chat_id,
        f"⏳ <b>Local Real-ESRGAN {scale}x</b> shuru ho raha hai…"
    )
    tmp_dir = tempfile.mkdtemp(dir=TEMP_DIR)
    try:
        in_path  = os.path.join(tmp_dir, f"input{ext}")
        out_ext  = ".png" if ext == ".webp" else ext
        out_path = os.path.join(tmp_dir, f"output{out_ext}")

        send_progress(bot, chat_id, status.message_id,
                      "📥 Image download ho rahi hai… 10%")
        logger.info(f"[ESRGAN] Downloading image file_id={file_id}")
        _download_telegram_file(bot, file_id, in_path)

        send_progress(bot, chat_id, status.message_id,
                      f"🔮 Real-ESRGAN {scale}x upscaling… 40%\n"
                      "⏳ CPU mode — 30-120s lagenge")

        from utils.image import upscale_image
        upscale_image(in_path, out_path, scale=scale)

        send_progress(bot, chat_id, status.message_id,
                      "📤 Enhanced image bhej raha hoon… 95%")

        with open(out_path, "rb") as f:
            img_bytes = f.read()

        bot.send_photo(
            chat_id,
            img_bytes,
            caption=(
                f"✅ <b>Local Real-ESRGAN {scale}x Done!</b>\n\n"
                f"📐 Scale: <b>{scale}x</b>\n"
                "🔮 Backend: <b>CPU (Python Real-ESRGAN)</b>"
            ),
            parse_mode="HTML"
        )
        bot.delete_message(chat_id, status.message_id)
        logger.info("[ESRGAN] Image upscaling complete.")

    except Exception as e:
        logger.error(f"[ESRGAN] Image error: {e}")
        send_progress(bot, chat_id, status.message_id, f"❌ <b>Error:</b> {e}")
    finally:
        delete_path(tmp_dir)
        cleanup_old_files()


# ── Video processing job ──────────────────────────────────────────

def process_local_video(bot, message, file_id: str, scale: int) -> None:
    """Download video, upscale frame-by-frame with local Real-ESRGAN, send result."""
    chat_id = message.chat.id
    status  = send_new_status(
        bot, chat_id,
        f"⏳ <b>Local Real-ESRGAN Video {scale}x</b> shuru ho raha hai…\n"
        "⚠️ Yeh CPU intensive hai — kaafi time lagega!"
    )
    tmp_dir  = tempfile.mkdtemp(dir=TEMP_DIR)
    in_path  = os.path.join(tmp_dir, "input.mp4")
    out_path = os.path.join(OUTPUT_DIR, f"esrgan_{chat_id}_{int(time.time())}.mp4")

    try:
        send_progress(bot, chat_id, status.message_id,
                      "📥 Video download ho rahi hai… 5%")
        logger.info(f"[ESRGAN] Downloading video file_id={file_id}")
        _download_telegram_file(bot, file_id, in_path)

        def _progress_cb(step: str, cur: int, total: int) -> None:
            if total > 0:
                pct = min(90, 10 + int(cur / total * 75))
                send_progress(bot, chat_id, status.message_id,
                              f"🔮 {step} ({cur}/{total})\n📊 {pct}%")
            else:
                send_progress(bot, chat_id, status.message_id, f"🔮 {step}")

        from utils.video import upscale_video
        upscale_video(in_path, out_path, scale=scale, progress_cb=_progress_cb)

        send_progress(bot, chat_id, status.message_id,
                      "📤 Enhanced video bhej raha hoon… 95%")

        send_path = _compress_if_needed(out_path)
        size_mb   = os.path.getsize(send_path) // (1024 * 1024)

        with open(send_path, "rb") as f:
            vid_bytes = f.read()

        bot.send_video(
            chat_id,
            ("enhanced.mp4", vid_bytes),
            caption=(
                f"✅ <b>Real-ESRGAN Video {scale}x Done!</b>\n\n"
                f"📦 Size: <b>{size_mb} MB</b>\n"
                f"🔮 Scale: <b>{scale}x</b> — frame-by-frame"
            ),
            parse_mode="HTML",
            supports_streaming=True
        )
        bot.delete_message(chat_id, status.message_id)
        logger.info("[ESRGAN] Video upscaling complete.")

    except Exception as e:
        logger.error(f"[ESRGAN] Video error: {e}")
        send_progress(bot, chat_id, status.message_id, f"❌ <b>Error:</b> {e}")
    finally:
        delete_path(tmp_dir)
        cleanup_old_files()


# ── Command handler registration ──────────────────────────────────

def register_upscale_handlers(bot) -> None:
    """Register /upscale and /upscale_video command handlers on the bot."""

    @bot.message_handler(commands=["upscale"])
    def cmd_upscale(message):
        parts = message.text.strip().split()
        scale = 4
        if len(parts) > 1 and parts[1] in ("2", "4"):
            scale = int(parts[1])
        pending_local_enhance[str(message.chat.id)] = {"scale": scale, "type": "image"}
        bot.reply_to(
            message,
            f"🔮 <b>Local Real-ESRGAN {scale}x Mode ON</b>\n\n"
            "📸 Image bhejo — local model se upscale karunga!\n"
            "<i>Note: CPU mode — 30-120 seconds lagenge</i>\n\n"
            "<b>Commands:</b>\n"
            "• /upscale 2 — 2x upscale\n"
            "• /upscale 4 — 4x upscale (default)",
            parse_mode="HTML"
        )

    @bot.message_handler(commands=["upscale_video"])
    def cmd_upscale_video(message):
        parts = message.text.strip().split()
        scale = 4
        if len(parts) > 1 and parts[1] in ("2", "4"):
            scale = int(parts[1])
        pending_local_enhance[str(message.chat.id)] = {"scale": scale, "type": "video"}
        bot.reply_to(
            message,
            f"🎥 <b>Local Real-ESRGAN Video {scale}x Mode ON</b>\n\n"
            "📹 Video bhejo — frame-by-frame upscale karunga!\n"
            "⚠️ <i>Max 5 min. CPU mode — kaafi time lagega.</i>",
            parse_mode="HTML"
        )

    logger.info("✅ Local Real-ESRGAN command handlers registered.")
