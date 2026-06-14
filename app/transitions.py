"""Fast, pretty transitions between clips via ffmpeg's xfade.

Unlike ``assembly.concat_clips`` (hard dip-to-black joins), this cross-dissolves
neighbouring clips with short, varied transitions (fade, wipe, slide, circle…)
for a snappy social-montage feel. All inputs are normalised to one canvas /
fps / pixel-format / SAR first so xfade doesn't choke on mismatched streams.
"""

from __future__ import annotations

from pathlib import Path

from . import config
from .ffmpeg_utils import probe_duration, run_ffmpeg

# A rotating set of quick, tasteful transitions (all built into ffmpeg xfade).
DEFAULT_TRANSITIONS = [
    "fade",
    "smoothleft",
    "wipeleft",
    "slideup",
    "circleopen",
    "smoothright",
    "wiperight",
    "slidedown",
    "fadeblack",
    "diagtl",
]


def _normalize(src: str, out: str, w: int, h: int, fps: int) -> str:
    """Re-encode a clip to a uniform canvas/fps/format so xfade can chain it."""
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
        f"fps={fps},format=yuv420p,setsar=1,settb=AVTB,setpts=PTS-STARTPTS"
    )
    run_ffmpeg(
        [
            "-i", src, "-vf", vf, "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", out,
        ]
    )
    return out


def xfade_montage(
    clip_paths: list[str],
    out_path: str,
    work_dir: Path,
    *,
    transition_dur: float = 0.35,
    transitions: list[str] | None = None,
    size: tuple[int, int] | None = None,
    fps: int | None = None,
) -> str:
    """Cross-dissolve ``clip_paths`` into one MP4 with short, varied transitions.

    Returns ``out_path``. A single clip is just normalised. Transition ``offset``
    for the k-th cut is (sum of the first k clip durations) - k*transition_dur.
    """
    if not clip_paths:
        raise ValueError("No clips to montage")

    w, h = size or (config.TRAILER_WIDTH, config.TRAILER_HEIGHT)
    f = fps or config.VIDEO_FPS
    trans = transitions or DEFAULT_TRANSITIONS
    work_dir.mkdir(parents=True, exist_ok=True)

    # 1) Normalise every clip to a common canvas/fps/format.
    norm: list[str] = []
    for i, src in enumerate(clip_paths):
        dst = str(work_dir / f"norm_{i:02d}.mp4")
        norm.append(_normalize(src, dst, w, h, f))

    if len(norm) == 1:
        Path(out_path).write_bytes(Path(norm[0]).read_bytes())
        return out_path

    durations = [probe_duration(p) or config.SCENE_DURATION for p in norm]
    t = float(transition_dur)
    # Keep the transition shorter than the shortest clip so xfade stays valid.
    t = max(0.1, min(t, min(durations) - 0.1))

    # 2) Build the xfade chain: [0][1]->[v1], [v1][2]->[v2], …
    inputs: list[str] = []
    for p in norm:
        inputs += ["-i", p]

    filt: list[str] = []
    prev = "[0:v]"
    cum = durations[0]
    for k in range(1, len(norm)):
        offset = cum - k * t
        if offset < 0.05:
            offset = 0.05
        tr = trans[(k - 1) % len(trans)]
        out_lbl = f"[v{k}]" if k < len(norm) - 1 else "[vout]"
        filt.append(
            f"{prev}[{k}:v]xfade=transition={tr}:duration={t:.3f}:offset={offset:.3f}{out_lbl}"
        )
        prev = out_lbl
        cum += durations[k]

    run_ffmpeg(
        [
            *inputs,
            "-filter_complex", ";".join(filt),
            "-map", "[vout]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", out_path,
        ]
    )
    return out_path
