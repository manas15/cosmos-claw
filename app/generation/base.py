"""Generation adapter interface.

A ``ClipGenerator`` turns a single ``Scene`` (an input photo/frame + a prompt +
timing) into a short video clip on disk. The rest of the loop (film -> cut ->
voice -> publish) is identical regardless of which generator is used.

This is the seam that lets us start with a free, local FFmpeg-based generator
and later swap in NVIDIA Cosmos 3 (running on a Nebius GPU) — or any other model
— without touching the UI or the videographer that films and cuts.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Scene:
    """One beat of a cut: a single photo turned into a short motion clip."""

    index: int
    source_path: str | None  # input photo/video for this scene (None = text-only)
    prompt: str  # generation prompt describing the desired motion/mood
    caption: str  # on-screen lower-third caption
    time_label: str  # e.g. "7:00 AM"
    time_of_day: str  # one of: morning | day | evening
    duration: float  # seconds
    # True when this beat stays in the same space as the previous one: the
    # videographer then seeds it with the previous clip's last frame for
    # continuous motion. False (default) starts fresh from this beat's photo.
    continues_prev: bool = False
    # --- cinematic motion (v2) ---
    # Camera move label from the brief (e.g. "dolly-in with parallax"). Drives
    # both the generation prompt and the stub's Ken Burns fallback.
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
        """Return (is_ready, human_readable_reason).

        This is a cheap, config-only check (e.g. "are the keys set?"). It does
        NOT touch the network, so it can't see a dropped tunnel or a provider
        outage — use ``live()`` for that.
        """
        return True, "ready"

    def live(self) -> bool:
        """Active liveness probe — actually reach the backend if it's remote.

        Local backends (and any that can't be probed cheaply) just report their
        ``available()`` status. Remote backends should override this with a real
        request so the loop can pause through a tunnel/provider blip instead of
        burning a generation. Must never raise.
        """
        try:
            ok, _ = self.available()
            return ok
        except Exception:  # noqa: BLE001
            return False
