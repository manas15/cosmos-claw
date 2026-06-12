"""Shared post-processing applied to every generated clip.

Whatever produced the raw clip (the local Ken Burns stub or a real Cosmos 3
generation), we finish it identically: normalize to the project resolution/fps,
apply a gentle time-of-day grade, burn in the time label + caption chips, and
fade in/out. This keeps a consistent look across backends and lets clips be
concatenated with a stream copy.
"""

from __future__ import annotations

from pathlib import Path

from . import config
from .ffmpeg_utils import probe_duration, run_ffmpeg
from .generation.base import Scene
from .text_overlay import render_chip, render_pill

# Per-time-of-day color grade (eq + colorbalance work in all ffmpeg builds).
_GRADES = {
    "morning": "eq=brightness=0.04:contrast=1.04:saturation=1.06,colorbalance=rs=0.06:bs=-0.04",
    "day": "eq=brightness=0.03:contrast=1.06:saturation=1.12",
    "evening": "eq=brightness=-0.05:contrast=1.06:saturation=1.05,colorbalance=rs=0.10:bs=-0.09",
}


def decorate_clip(raw_path: str, out_path: str, scene: Scene) -> str:
    w, h, fps = config.VIDEO_WIDTH, config.VIDEO_HEIGHT, config.VIDEO_FPS
    work = Path(out_path).parent
    margin = 46
    fade = 0.5

    duration = probe_duration(raw_path) or scene.duration
    fade_out_start = max(0.0, duration - fade)
    grade = _GRADES.get(scene.time_of_day, _GRADES["day"])

    time_png = render_chip(
        scene.time_label, str(work / f"time_{scene.index}.png"), font_size=28
    )
    cap_png = render_chip(
        scene.caption,
        str(work / f"cap_{scene.index}.png"),
        font_size=34,
        max_text_width=w - 160,
    )

    filtergraph = (
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},fps={fps},setsar=1,{grade}[bg];"
        f"[bg][1:v]overlay={margin}:{margin}[s1];"
        f"[s1][2:v]overlay=(W-w)/2:H-h-{margin}[s2];"
        f"[s2]fade=t=in:st=0:d={fade},"
        f"fade=t=out:st={fade_out_start:.2f}:d={fade},format=yuv420p[out]"
    )

    run_ffmpeg(
        [
            "-i", raw_path,
            "-i", time_png,
            "-i", cap_png,
            "-filter_complex", filtergraph,
            "-map", "[out]",
            "-r", str(fps),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            out_path,
        ]
    )
    return out_path


# Near-neutral grade: keep colors true to the real photo (just a touch of
# contrast/saturation so it doesn't look flat). Realism over "look".
_TRAILER_GRADE = "eq=contrast=1.02:saturation=1.03"


def decorate_trailer_clip(
    raw_path: str,
    out_path: str,
    scene: Scene,
    *,
    title: str | None = None,
) -> str:
    """Finish a clip for the VERTICAL (9:16) listing trailer.

    The landscape source clip is centered over a blurred, darkened fill of
    itself (the standard Reels look), graded, then a selling-point caption is
    placed in the lower third (and an optional title pill near the top on the
    opening beat). Fades in/out for clean transitions.
    """
    w, h, fps = config.TRAILER_WIDTH, config.TRAILER_HEIGHT, config.VIDEO_FPS
    work = Path(out_path).parent
    fade = 0.4

    duration = probe_duration(raw_path) or scene.duration
    fade_out_start = max(0.0, duration - fade)

    # Base composite: blurred fill + centered, graded foreground clip.
    chains = [
        "[0:v]split=2[fg0][bg0]",
        (
            f"[bg0]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},boxblur=24:2,eq=brightness=-0.12[bg]"
        ),
        f"[fg0]scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"setsar=1,{_TRAILER_GRADE}[fg]",
        "[bg][fg]overlay=(W-w)/2:(H-h)/2[base]",
    ]

    extra_inputs: list[str] = []
    cur = "[base]"
    in_idx = 1  # 0 is the raw clip

    if title:
        title_png = render_pill(title, str(work / f"ttl_{scene.index}.png"), font_size=46)
        extra_inputs += ["-i", title_png]
        chains.append(f"{cur}[{in_idx}:v]overlay=(W-w)/2:170[t1]")
        cur = "[t1]"
        in_idx += 1

    if scene.caption:
        cap_png = render_chip(
            scene.caption,
            str(work / f"tcap_{scene.index}.png"),
            font_size=46,
            max_text_width=w - 160,
            pad_x=30,
            pad_y=18,
        )
        extra_inputs += ["-i", cap_png]
        chains.append(f"{cur}[{in_idx}:v]overlay=(W-w)/2:H-h-90[c1]")
        cur = "[c1]"
        in_idx += 1

    chains.append(
        f"{cur}fps={fps},fade=t=in:st=0:d={fade},"
        f"fade=t=out:st={fade_out_start:.2f}:d={fade},format=yuv420p[out]"
    )
    filtergraph = ";".join(chains)

    run_ffmpeg(
        [
            "-i", raw_path,
            *extra_inputs,
            "-filter_complex", filtergraph,
            "-map", "[out]",
            "-r", str(fps),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            out_path,
        ]
    )
    return out_path
