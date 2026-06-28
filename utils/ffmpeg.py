import shutil
import subprocess
import logging
import os

logger = logging.getLogger(__name__)


def check_ffmpeg() -> bool:
    """Return True if ffmpeg is available in PATH."""
    return shutil.which("ffmpeg") is not None


def require_ffmpeg() -> None:
    """Raise RuntimeError with helpful message if ffmpeg is missing."""
    if not check_ffmpeg():
        raise RuntimeError(
            "FFmpeg not found. Please install it:\n"
            "  Replit: add 'ffmpeg' to replit.nix packages\n"
            "  Linux: sudo apt install ffmpeg"
        )


def extract_frames(video_path: str, frames_dir: str, fps: float = None) -> int:
    """
    Extract frames from video_path into frames_dir as frame_%06d.png.
    If fps is None, extract every frame.
    Returns the number of frames extracted.
    """
    require_ffmpeg()
    os.makedirs(frames_dir, exist_ok=True)
    vf = f"fps={fps}" if fps else "fps=source_fps"
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", vf,
        "-vsync", "0",
        os.path.join(frames_dir, "frame_%06d.png")
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"Frame extraction failed:\n{result.stderr.decode(errors='ignore')[-400:]}")
    frames = [f for f in os.listdir(frames_dir) if f.startswith("frame_") and f.endswith(".png")]
    count = len(frames)
    logger.info(f"Extracted {count} frames from {video_path}")
    return count


def merge_frames_to_video(
    frames_dir: str,
    audio_path: str,
    output_path: str,
    fps: float,
    crf: int = 18,
) -> None:
    """
    Merge upscaled frames back into an MP4 with the original audio.
    """
    require_ffmpeg()
    frame_pattern = os.path.join(frames_dir, "frame_%06d.png")
    if audio_path and os.path.exists(audio_path):
        cmd = [
            "ffmpeg", "-y",
            "-framerate", str(fps),
            "-i", frame_pattern,
            "-i", audio_path,
            "-c:v", "libx264", "-preset", "slow", "-crf", str(crf),
            "-c:a", "aac", "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            output_path
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-framerate", str(fps),
            "-i", frame_pattern,
            "-c:v", "libx264", "-preset", "slow", "-crf", str(crf),
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            output_path
        ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"Frame merge failed:\n{result.stderr.decode(errors='ignore')[-400:]}")
    logger.info(f"Merged video saved to {output_path}")


def extract_audio(video_path: str, audio_path: str) -> bool:
    """
    Extract audio from video as WAV. Returns True if audio was found, False if silent.
    """
    require_ffmpeg()
    result = subprocess.run([
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2",
        audio_path
    ], capture_output=True)
    return result.returncode == 0 and os.path.exists(audio_path) and os.path.getsize(audio_path) > 0


def get_video_fps(video_path: str) -> float:
    """Detect FPS of a video using ffprobe."""
    try:
        result = subprocess.run([
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path
        ], capture_output=True, text=True, timeout=15)
        frac = result.stdout.strip()
        if "/" in frac:
            num, den = frac.split("/")
            return float(num) / float(den)
        return float(frac)
    except Exception:
        return 30.0
