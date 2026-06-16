"""Luma Dream Machine image->video adapter.

Optional dependency: ``pip install lumaai``. Set ``LUMA_API_KEY``.
Uses a single start keyframe (``frame0``) so motion begins from the scene photo.
"""

from __future__ import annotations

import time
from pathlib import Path

import requests

from ... import config
from ..base import ClipGenerator, Scene
from ._common import full_prompt, image_data_uri, scene_image

_MODEL = "ray-2"


class LumaClipGenerator(ClipGenerator):
    name = "luma (Dream Machine)"

    def available(self) -> tuple[bool, str]:
        if not config.LUMA_API_KEY:
            return False, "LUMA_API_KEY not set"
        return True, f"configured ({_MODEL})"

    def generate_clip(self, scene: Scene, out_path: str, variant: int = 0) -> str:
        ok, why = self.available()
        if not ok:
            raise RuntimeError(why)
        try:
            from lumaai import LumaAI  # optional dependency
        except ImportError as exc:  # noqa: BLE001
            raise RuntimeError("luma backend needs `pip install lumaai`") from exc

        img = scene_image(scene, Path(out_path).parent)
        client = LumaAI(auth_token=config.LUMA_API_KEY)
        gen = client.generations.create(
            model=_MODEL,
            prompt=full_prompt(scene),
            keyframes={"frame0": {"type": "image", "url": image_data_uri(img)}},
            resolution="720p",
            aspect_ratio="9:16",
        )
        deadline = time.time() + config.VIDEO_TIMEOUT
        while time.time() < deadline:
            time.sleep(5)
            g = client.generations.get(id=gen.id)
            if g.state == "completed":
                url = g.assets.video
                dl = requests.get(url, timeout=config.VIDEO_TIMEOUT)
                dl.raise_for_status()
                Path(out_path).write_bytes(dl.content)
                return out_path
            if g.state == "failed":
                raise RuntimeError(f"Luma generation failed: {getattr(g, 'failure_reason', '')}")
        raise RuntimeError("Luma generation timed out")
