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

    def generate_clip(self, scene: Scene, out_path: str, variant: int = 0) -> str:
        """Produce a raw, motion-only clip (no captions/fades; decorate adds those).

        The Ken Burns move is shaped by ``scene.shot`` and ``scene.motion_strength``
        so the stub previews the director's intent, and ``variant`` perturbs the
        move (direction/amplitude/speed) so best-of-N takes are visibly different.
        """
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
        z_expr, x_expr, y_expr = self._motion_exprs(scene, total_frames, variant)

        # Fed a SINGLE still frame; zoompan's d= generates all output frames
        # (do NOT -loop the input, or d multiplies per input frame).
        vf = (
            "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,"
            f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':"
            f"d={total_frames}:s={self.w}x{self.h}:fps={self.fps},"
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

    def _motion_exprs(self, scene: Scene, total_frames: int, variant: int) -> tuple[str, str, str]:
        """Build zoompan z/x/y expressions for the scene's shot + motion strength.

        Returns expressions in terms of ``on`` (output frame index) and ``zoom``.
        """
        n = max(1, total_frames)
        strength = min(1.0, max(0.0, float(scene.motion_strength or 0.5)))
        shot = (scene.shot or "slow push-in").lower()

        # Per-variant perturbation: alternate speed and (for some takes) direction.
        speed = (1.0, 0.82, 1.18, 0.92)[variant % 4]
        flip = -1.0 if variant in (2, 3) else 1.0

        # Total zoom travel (fraction) and pan/tilt travel (pixels), scaled by strength.
        zoom_amp = (0.10 + 0.30 * strength) * speed
        pan_px = (40.0 + 220.0 * strength) * speed

        zoom_in = "pull" not in shot  # pull-back zooms out, everything else in
        if zoom_in:
            z_end = 1.0 + zoom_amp
            inc = zoom_amp / n
            z_expr = f"min(zoom+{inc:.6f}\\,{z_end:.4f})"
        else:
            # Start zoomed in, ease back toward 1.0 over the clip.
            z_start = 1.0 + zoom_amp
            z_expr = f"max({z_start:.4f}-{(zoom_amp / n):.6f}*on\\,1.0)"

        dx = dy = 0.0
        if "pan left" in shot:
            dx = -pan_px * flip
        elif "pan right" in shot:
            dx = pan_px * flip
        elif "tilt up" in shot or "crane" in shot:
            dy = -pan_px * flip
        elif "parallax" in shot or "drift" in shot:
            dx = pan_px * flip
            dy = -0.4 * pan_px * flip
        elif "arc" in shot:
            dx = 0.8 * pan_px * flip
            dy = -0.3 * pan_px * flip
        elif "doorway" in shot:
            dy = -0.2 * pan_px * flip  # push-in handles the forward feel
        # push-in / pull-back / rack focus: centered (zoom carries the motion).

        cx = f"iw/2-(iw/zoom/2)+({dx:.2f})*on/{n}"
        cy = f"ih/2-(ih/zoom/2)+({dy:.2f})*on/{n}"
        return z_expr, cx, cy
