"""Turn raw user inputs into a timed scene plan (the storyboard).

Kept deliberately simple for the first version: cycle the uploaded media through
a morning -> day -> evening rhythm, attach a time-of-day grade and a caption.
The user's instructions become the narrative theme woven into captions.
"""

from __future__ import annotations

from . import config
from .generation.base import Scene

# (time_of_day, clock label, default caption fragment)
_TIME_SLOTS = [
    ("morning", "7:00 AM", "Morning light fills the space"),
    ("day", "1:00 PM", "Easy afternoon at home"),
    ("evening", "6:30 PM", "Evenings wind down here"),
]


def _clean(text: str, limit: int = 90) -> str:
    text = " ".join((text or "").split())
    return text[:limit]


def build_storyboard(media_paths: list[str], instructions: str) -> list[Scene]:
    theme = _clean(instructions, limit=120)
    scenes: list[Scene] = []
    for i, path in enumerate(media_paths):
        tod, label, default_caption = _TIME_SLOTS[i % len(_TIME_SLOTS)]
        # Prefer the user's instruction as the caption theme on the first scene,
        # then fall back to the time-of-day flavor text.
        caption = theme if (i == 0 and theme) else default_caption
        prompt = (
            f"Same room, {tod} lighting, gentle camera drift, photorealistic, "
            f"no people. {theme}".strip()
        )
        scenes.append(
            Scene(
                index=i,
                source_path=path,
                prompt=prompt,
                caption=caption,
                time_label=label,
                time_of_day=tod,
                duration=config.SCENE_DURATION,
            )
        )
    return scenes
