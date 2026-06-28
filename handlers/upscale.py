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


TELEGRAM_PHOTO_MAX = 10 * 1024 * 1024   # 10 MB — send_photo limit
TELEGRAM_DOC_MAX  = 50 * 1024 * 1024   # 50 MB — send_document limit


def _compress_image_for_telegram(path: str) -> str:
    """
    Compress image so it fits Telegram limits.
    Returns path to a JPEG file <= TELEGRAM_PHOTO_MAX if possible,
    otherwise a file <= TELEGRAM_DOC_MAX (caller should use send_document).
    """
    size = os.path.getsize(path)
    if size <= TELEGRAM_PHOTO_MAX:
        return path

    import cv2
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        return path  # Can't read — let caller handle the error

    base = os.path.splitext(path)[0]
    out_path = base + "_tg.jpg"

    # Try progressive JPEG quality reduction
    for quality in (88, 75, 62, 50, 38):
        cv2.imwrite(out_path, img, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if os.path.getsize(out_path) <= TELEGRAM_PHOTO_MAX:
            logger.info(f"[ESRGAN] Compressed to {os.path.getsize(out_path)//1024}KB (quality={quality})")
            return out_path

    # Still too big for photo — shrink dimensions so it fits as document
    h, w = img.shape[:2]
    for factor in (0.75, 0.5, 0.35):
        small = cv2.resize(img, (int(w * factor), int(h * factor)), interpolation=cv2.INTER_LANCZOS4)
        cv2.imwrite(out_path, small, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if os.path.getsize(out_path) <= TELEGRAM_DOC_MAX:
            logger.info(f"[ESRGAN] Resized+compressed for doc: {os.path.getsize(out_path)//1024}KB")
            return out_path

    return out_path


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

def process_local_image(bot, message, file_id: str, scale: int, ext: str = ".jpg",
                        on_success=None, on_failure=None) -> None:
    """Download image, upscale with local Real-ESRGAN, send result.
    on_success() called only after successful delivery.
    on_failure() called if processing/sending fails (pass to refund credits)."""
    chat_id = message.chat.id
    status  = send_new_status(
        bot, chat_id,
        f"⏳ <b>Image upscaling {scale}x</b> shuru ho raha hai…"
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
                      f"🔮 {scale}x upscaling ho rahi hai… 40%\n"
                      "⏳ 10-30 sec lagenge")

        from utils.image import upscale_image
        upscale_image(in_path, out_path, scale=scale)

        send_progress(bot, chat_id, status.message_id,
                      "📦 File size check + compress ho raha hai… 80%")

        # Compress if needed so it fits Telegram limits
        send_path = _compress_image_for_telegram(out_path)
        final_size = os.path.getsize(send_path)

        send_progress(bot, chat_id, status.message_id,
                      "📤 Enhanced image bhej raha hoon… 95%")

        caption = (
            f"✅ <b>Image {scale}x Enhanced!</b>\n\n"
            f"📐 Scale: <b>{scale}x</b>\n"
            f"📦 Size: <b>{final_size // 1024}KB</b>\n"
            "🔮 Backend: <b>OpenCV Lanczos4 + AI Sharpen</b>"
        )

        with open(send_path, "rb") as f:
            img_bytes = f.read()

        if final_size <= TELEGRAM_PHOTO_MAX:
            bot.send_photo(chat_id, img_bytes, caption=caption, parse_mode="HTML")
        else:
            # Too big for photo — send as file (supports up to 50 MB)
            bot.send_document(
                chat_id,
                (os.path.basename(send_path), img_bytes, "image/jpeg"),
                caption=caption + "\n<i>(File ke roop mein bheja — size badi thi)</i>",
                parse_mode="HTML"
            )

        bot.delete_message(chat_id, status.message_id)
        logger.info("[ESRGAN] Image upscaling complete.")
        if on_success:
            on_success()

    except Exception as e:
        logger.error(f"[ESRGAN] Image error: {e}")
        send_progress(bot, chat_id, status.message_id, f"❌ <b>Enhancement failed:</b> {e}")
        if on_failure:
            on_failure()
    finally:
        delete_path(tmp_dir)
        cleanup_old_files()


# ── Video processing job ──────────────────────────────────────────

def process_local_video(bot, message, file_id: str, scale: int,
                        on_success=None, on_failure=None) -> None:
    """Download video, upscale frame-by-frame with local Real-ESRGAN, send result.
    on_success() called only after successful delivery.
    on_failure() called if processing/sending fails (pass to refund credits)."""
    chat_id = message.chat.id
    status  = send_new_status(
        bot, chat_id,
        f"⏳ <b>Video upscaling {scale}x</b> shuru ho raha hai…\n"
        "⚠️ CPU mode — kaafi time lagega!"
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
                f"✅ <b>Video {scale}x Enhanced!</b>\n\n"
                f"📦 Size: <b>{size_mb} MB</b>\n"
                f"🔮 Scale: <b>{scale}x</b> — frame-by-frame"
            ),
            parse_mode="HTML",
            supports_streaming=True
        )
        bot.delete_message(chat_id, status.message_id)
        logger.info("[ESRGAN] Video upscaling complete.")
        if on_success:
            on_success()

    except Exception as e:
        logger.error(f"[ESRGAN] Video error: {e}")
        send_progress(bot, chat_id, status.message_id, f"❌ <b>Enhancement failed:</b> {e}")
        if on_failure:
            on_failure()
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
