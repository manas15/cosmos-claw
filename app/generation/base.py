"""Generation adapter interface.

A ``ClipGenerator`` turns a single storyboard ``Scene`` (an input photo/frame +
a prompt + timing) into a short video clip on disk. The rest of the pipeline
(storyboard -> generate clips -> assemble final MP4) is identical regardless of
which generator is used.

This is the seam that lets us start with a free, local FFmpeg-based generator
and later swap in NVIDIA Cosmos 3 (running on a Nebius GPU) without touching the
UI, the pipeline, or the assembly code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Scene:
    """One segment of the day-in-the-life storyboard."""

    index: int
    source_path: str | None  # input photo/video for this scene (None = text-only)
    prompt: str  # generation prompt describing the desired motion/mood
    caption: str  # on-screen lower-third caption
    time_label: str  # e.g. "7:00 AM"
    time_of_day: str  # one of: morning | day | evening
    duration: float  # seconds
    # True when this beat stays in the same space as the previous one: the
    # pipeline then seeds it with the previous clip's last frame for continuous
    # motion. False (default) starts fresh from this beat's room photo.
    continues_prev: bool = False
    # --- cinematic motion (v2) ---
    # Camera move label chosen by the director (e.g. "dolly-in with parallax").
    # Drives both the generation prompt and the stub's Ken Burns fallback.
    shot: str = ""
    # How bold the camera move is, 0..1 (0.35 calm, 0.85 dramatic). Scales the
    # generator's flow_shift and the stub's pan/zoom amplitude.
    motion_strength: float = 0.5
    # Subtle in-scene motion to bring the frame alive without changing the room
    # (e.g. "sheer curtains sway", "steam rising from the cup", "water shimmers").
    ambient: str = ""


class ClipGenerator(ABC):
    """Produces a video clip for a single scene."""

    name: str = "base"

    @abstractmethod
    def generate_clip(self, scene: Scene, out_path: str, variant: int = 0) -> str:
        """Render ``scene`` to ``out_path`` (an .mp4) and return the path.

        ``variant`` selects a distinct take (different seed / motion treatment)
        for best-of-N generation; variant 0 is the canonical take.
        """
        raise NotImplementedError

    def available(self) -> tuple[bool, str]:
        """Return (is_ready, human_readable_reason)."""
        return True, "ready"
