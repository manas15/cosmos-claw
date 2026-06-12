"""Thin helpers around the ffmpeg/ffprobe CLIs."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class FFmpegError(RuntimeError):
    pass


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def run_ffmpeg(args: list[str]) -> None:
    """Run ffmpeg with the given args (a leading 'ffmpeg' is added)."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FFmpegError(
            f"ffmpeg failed (exit {proc.returncode}):\n{proc.stderr.strip()}\ncmd: {' '.join(cmd)}"
        )


def probe_duration(path: str) -> float:
    """Return the media duration in seconds (0.0 if unknown)."""
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        capture_output=True,
        text=True,
    )
    try:
        return float(proc.stdout.strip())
    except (ValueError, AttributeError):
        return 0.0


def extract_frame(video_path: str, out_png: str, at_seconds: float = 1.0) -> str:
    """Grab a single representative frame from a video as a PNG."""
    run_ffmpeg(
        [
            "-ss",
            str(at_seconds),
            "-i",
            video_path,
            "-frames:v",
            "1",
            out_png,
        ]
    )
    if not Path(out_png).exists():
        # Fall back to the very first frame if the seek landed past the end.
        run_ffmpeg(["-i", video_path, "-frames:v", "1", out_png])
    return out_png


def extract_poster(video_path: str, out_jpg: str, at_seconds: float = 2.0) -> str:
    """Grab a clean cover frame (JPEG) from inside the video for use as a poster."""
    run_ffmpeg(
        ["-ss", str(at_seconds), "-i", video_path, "-frames:v", "1", "-q:v", "3", out_jpg]
    )
    if not Path(out_jpg).exists():
        run_ffmpeg(["-i", video_path, "-frames:v", "1", "-q:v", "3", out_jpg])
    return out_jpg


def extract_last_frame(video_path: str, out_png: str) -> str:
    """Grab the final frame of a clip as a PNG (used to chain clip -> clip)."""
    # -sseof seeks relative to the end; decode the last ~1s and -update keeps
    # overwriting so the file ends up holding the very last frame.
    run_ffmpeg(["-sseof", "-1", "-i", video_path, "-update", "1", "-q:v", "2", out_png])
    if not Path(out_png).exists():
        # Fallback: reverse the (short) clip and take its first frame.
        run_ffmpeg(["-i", video_path, "-vf", "reverse", "-frames:v", "1", out_png])
    return out_png


def convert_image(src: str, out_jpg: str) -> str:
    """Convert any image (incl. AVIF/HEIC) to a baseline JPEG.

    Tries Pillow first; falls back to macOS ``sips`` which handles AVIF/HEIC
    natively. Returns the path that actually holds a readable image (``out_jpg``
    on success, else the original ``src``).
    """
    try:
        from PIL import Image

        with Image.open(src) as im:
            im.convert("RGB").save(out_jpg, format="JPEG", quality=92)
        return out_jpg
    except Exception:
        pass

    if shutil.which("sips"):
        proc = subprocess.run(
            ["sips", "-s", "format", "jpeg", src, "--out", out_jpg],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0 and Path(out_jpg).exists():
            return out_jpg

    return src


def write_textfile(path: str, text: str) -> str:
    """Write caption text to a file so ffmpeg's drawtext can read it verbatim,
    avoiding fragile shell/filter escaping of user-provided strings."""
    Path(path).write_text(text, encoding="utf-8")
    return path
