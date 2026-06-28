"""
Video upscaling via frame-by-frame Real-ESRGAN processing.
Supports MP4, MOV, MKV, AVI. Output is always MP4.
"""
import os
import logging
import time
import shutil
import tempfile

from utils.config import TEMP_DIR, OUTPUT_DIR
from utils.ffmpeg import extract_frames, merge_frames_to_video, extract_audio, get_video_fps
from utils.image import upscale_image
from utils.cleanup import delete_path

logger = logging.getLogger(__name__)


def upscale_video(
    input_path: str,
    output_path: str,
    scale: int = 4,
    progress_cb=None,
    crf: int = 18,
) -> str:
    """
    Full video upscaling pipeline:
      1. Detect FPS
      2. Extract audio
      3. Extract frames
      4. Upscale each frame with Real-ESRGAN
      5. Merge frames + audio back to MP4

    progress_cb(step: str, current: int, total: int) — optional progress callback.
    Returns output_path on success.
    """
    t0 = time.time()
    logger.info(f"Starting video upscale: {input_path} -> {output_path} (scale={scale}x)")

    work_dir = tempfile.mkdtemp(dir=TEMP_DIR)
    frames_dir      = os.path.join(work_dir, "frames")
    upscaled_dir    = os.path.join(work_dir, "upscaled")
    audio_path      = os.path.join(work_dir, "audio.wav")
    os.makedirs(frames_dir, exist_ok=True)
    os.makedirs(upscaled_dir, exist_ok=True)

    try:
        # Step 1 — detect FPS
        fps = get_video_fps(input_path)
        logger.info(f"Video FPS: {fps}")

        # Step 2 — extract audio
        if progress_cb:
            progress_cb("Extracting audio…", 0, 0)
        has_audio = extract_audio(input_path, audio_path)

        # Step 3 — extract frames
        if progress_cb:
            progress_cb("Extracting frames…", 0, 0)
        total_frames = extract_frames(input_path, frames_dir)
        logger.info(f"Frame count: {total_frames}")

        if total_frames == 0:
            raise RuntimeError("No frames extracted — video may be corrupted.")

        # Step 4 — upscale each frame sequentially
        frame_files = sorted([
            f for f in os.listdir(frames_dir)
            if f.startswith("frame_") and f.endswith(".png")
        ])

        for i, fname in enumerate(frame_files, start=1):
            in_frame  = os.path.join(frames_dir,   fname)
            out_frame = os.path.join(upscaled_dir, fname)
            upscale_image(in_frame, out_frame, scale=scale)
            if progress_cb:
                progress_cb(f"Upscaling frame {i}/{total_frames}…", i, total_frames)
            if i % 10 == 0:
                logger.info(f"Upscaled {i}/{total_frames} frames")

        # Step 5 — merge frames + audio
        if progress_cb:
            progress_cb("Merging video…", 0, 0)
        merge_frames_to_video(
            frames_dir=upscaled_dir,
            audio_path=audio_path if has_audio else None,
            output_path=output_path,
            fps=fps,
            crf=crf,
        )

        elapsed = time.time() - t0
        logger.info(f"Video upscaling complete in {elapsed:.1f}s -> {output_path}")
        return output_path

    finally:
        delete_path(work_dir)
