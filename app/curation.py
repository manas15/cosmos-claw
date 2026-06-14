"""Best-of-N take scoring.

When the pipeline renders several takes for one beat (varied seed / motion), we
need to auto-pick the strongest one. We want clips that have real, *cinematic*
motion but stay *smooth and stable* — i.e. avoid both dead-static takes and the
warping/flicker failure mode where the model tears the frame apart.

The heuristic samples a handful of frames and measures:
  * motion energy   = mean absolute frame-to-frame difference,
  * smoothness      = 1 / (1 + coefficient-of-variation of those diffs).

The score peaks at a target motion level (smooth, deliberate movement) and is
multiplied by smoothness, so erratic/warpy takes are penalized even if they move
a lot. Everything is deterministic and dependency-light (ffmpeg + Pillow).
"""

from __future__ import annotations

import math
import tempfile
from pathlib import Path

from .ffmpeg_utils import run_ffmpeg

# Target mean frame-diff (on a 0..1 scale). Tuned for "deliberate cinematic
# motion" — above dead-static, below warpy. Override via the pipeline if needed.
_TARGET_MOTION = 0.055
_MOTION_SPREAD = 0.045
_SAMPLE_FPS = 4
_SAMPLE_W, _SAMPLE_H = 96, 54


def _sample_frames(video_path: str, work: Path) -> list[list[float]]:
    """Return a list of small grayscale frames as flat float lists (0..1)."""
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001 - Pillow always present, but be defensive
        return []

    tmp = Path(tempfile.mkdtemp(prefix="take_", dir=str(work)))
    pattern = str(tmp / "f_%03d.png")
    try:
        run_ffmpeg(
            [
                "-i", video_path,
                "-vf", f"fps={_SAMPLE_FPS},scale={_SAMPLE_W}:{_SAMPLE_H}",
                pattern,
            ]
        )
    except Exception:  # noqa: BLE001
        return []

    frames: list[list[float]] = []
    for png in sorted(tmp.glob("f_*.png")):
        try:
            with Image.open(png) as im:
                g = im.convert("L")
                frames.append([px / 255.0 for px in g.getdata()])
        except Exception:  # noqa: BLE001
            continue
    return frames


def score_take(video_path: str, work: Path) -> dict:
    """Score one take. Returns {score, motion, smoothness, frames}."""
    frames = _sample_frames(video_path, work)
    if len(frames) < 2:
        # Can't measure motion; give it a neutral-low score so a measurable
        # take wins, but it isn't disqualified outright.
        return {"score": 0.25, "motion": 0.0, "smoothness": 0.0, "frames": len(frames)}

    diffs: list[float] = []
    for a, b in zip(frames, frames[1:]):
        n = len(a)
        s = 0.0
        for i in range(n):
            s += abs(a[i] - b[i])
        diffs.append(s / n)

    motion = sum(diffs) / len(diffs)
    if motion <= 1e-6:
        return {"score": 0.05, "motion": 0.0, "smoothness": 1.0, "frames": len(frames)}

    mean = motion
    var = sum((d - mean) ** 2 for d in diffs) / len(diffs)
    cv = math.sqrt(var) / mean  # coefficient of variation (jerkiness)
    smoothness = 1.0 / (1.0 + cv)

    # Gaussian reward peaking at the target motion level.
    motion_score = math.exp(-(((motion - _TARGET_MOTION) / _MOTION_SPREAD) ** 2))
    score = motion_score * (0.5 + 0.5 * smoothness)

    return {
        "score": round(score, 4),
        "motion": round(motion, 4),
        "smoothness": round(smoothness, 4),
        "frames": len(frames),
    }


def rank_takes(video_paths: list[str], work: Path) -> list[dict]:
    """Score every take; return entries sorted best-first.

    Each entry: {index, path, score, motion, smoothness, frames}. ``index`` is the
    take's original position in ``video_paths``.
    """
    scored: list[dict] = []
    for i, path in enumerate(video_paths):
        metrics = score_take(path, work)
        scored.append({"index": i, "path": path, **metrics})
    scored.sort(key=lambda e: e["score"], reverse=True)
    return scored


def pick_best(video_paths: list[str], work: Path) -> tuple[int, list[dict]]:
    """Return (best_index, ranked_takes). best_index is into ``video_paths``."""
    if not video_paths:
        return 0, []
    ranked = rank_takes(video_paths, work)
    return ranked[0]["index"], ranked
