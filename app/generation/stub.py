"""Local, free clip generator built on FFmpeg.

This is the "simplest version first" stand-in for a real world model. It does
NOT hallucinate new video; instead it animates the user's actual photo with a
slow Ken Burns move. Captions, grading, and fades are added afterwards by the
shared ``decorate`` step, so the stub and the real Cosmos backend produce
identically-finished clips.

The result is a real, shareable MP4 that exercises the entire Cosmos Claw pipeline
end-to-end with zero GPU and zero cloud cost.
"""

from __future__ import annotations

from pathlib import Path

from .. import config
from ..ffmpeg_utils import extract_frame, ffmpeg_available, run_ffmpeg
from .base import ClipGenerator, Scene


class StubClipGenerator(ClipGenerator):
    name = "stub (local FFmpeg)"

    def __init__(self) -> None:
        self.w = config.VIDEO_WIDTH
        self.h = config.VIDEO_HEIGHT
        self.fps = config.VIDEO_FPS

    def available(self) -> tuple[bool, str]:
        if not ffmpeg_available():
            return False, "ffmpeg/ffprobe not found on PATH"
        return True, "ready"

    def generate_clip(self, scene: Scene, out_path: str) -> str:
        """Produce a raw, motion-only clip (no captions/fades; decorate adds those)."""
        if not scene.source_path:
            raise ValueError("StubClipGenerator requires a source image/video for the scene")

        work = Path(out_path).parent
        src = scene.source_path

        # If the input is a video, pull a representative still and animate that.
        ext = Path(src).suffix.lower()
        if ext in config.ALLOWED_VIDEO_EXTS:
            frame = str(work / f"frame_{scene.index}.png")
            src = extract_frame(src, frame, at_seconds=1.0)

        total_frames = int(round(scene.duration * self.fps))

        # Fed a SINGLE still frame; zoompan's d= generates all output frames
        # (do NOT -loop the input, or d multiplies per input frame).
        vf = (
            "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,"
            "zoompan=z='min(zoom+0.0012,1.40)':x='iw/2-(iw/zoom/2)':"
            f"y='ih/2-(ih/zoom/2)':d={total_frames}:s={self.w}x{self.h}:fps={self.fps},"
            "format=yuv420p"
        )

        run_ffmpeg(
            [
                "-i", src,
                "-vf", vf,
                "-r", str(self.fps),
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "20",
                "-pix_fmt", "yuv420p",
                out_path,
            ]
        )
        return out_path
