"""Shared helpers for the provider adapters."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from ... import config
from ...ffmpeg_utils import extract_frame
from ..base import Scene


def scene_image(scene: Scene, out_dir: Path) -> str:
    """Return a still-image path for the scene (extract a frame if it's a video)."""
    if not scene.source_path:
        raise ValueError("this backend requires a source image for the scene")
    src = scene.source_path
    if Path(src).suffix.lower() in config.ALLOWED_VIDEO_EXTS:
        src = extract_frame(src, str(out_dir / f"frame_{scene.index}.png"), at_seconds=1.0)
    return src


def image_data_uri(path: str) -> str:
    mime = mimetypes.guess_type(path)[0] or "image/png"
    b64 = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def full_prompt(scene: Scene) -> str:
    return scene.prompt.rstrip() + config.COSMOS_FIDELITY_SUFFIX
