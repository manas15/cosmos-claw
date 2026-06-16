"""Runway (Gen-3/Gen-4) image->video adapter.

Optional dependency: ``pip install runwayml``. Set ``RUNWAY_API_KEY``.
Reference implementation against the official ``runwayml`` SDK's
``image_to_video`` task (submit + poll). Tweak ``model`` to your access tier.
"""

from __future__ import annotations

import time
from pathlib import Path

import requests

from ... import config
from ..base import ClipGenerator, Scene
from ._common import full_prompt, image_data_uri, scene_image

_MODEL = "gen4_turbo"


class RunwayClipGenerator(ClipGenerator):
    name = "runway (Gen-4)"

    def available(self) -> tuple[bool, str]:
        if not config.RUNWAY_API_KEY:
            return False, "RUNWAY_API_KEY not set"
        return True, f"configured ({_MODEL})"

    def generate_clip(self, scene: Scene, out_path: str, variant: int = 0) -> str:
        ok, why = self.available()
        if not ok:
            raise RuntimeError(why)
        try:
            from runwayml import RunwayML  # optional dependency
        except ImportError as exc:  # noqa: BLE001
            raise RuntimeError("runway backend needs `pip install runwayml`") from exc

        img = scene_image(scene, Path(out_path).parent)
        client = RunwayML(api_key=config.RUNWAY_API_KEY)
        task = client.image_to_video.create(
            model=_MODEL,
            prompt_image=image_data_uri(img),
            prompt_text=full_prompt(scene),
            ratio="720:1280",
            duration=5,
            seed=1000 + scene.index * 100 + variant * 17,
        )
        task_id = task.id
        deadline = time.time() + config.VIDEO_TIMEOUT
        while time.time() < deadline:
            time.sleep(5)
            t = client.tasks.retrieve(task_id)
            if t.status == "SUCCEEDED":
                url = t.output[0]
                dl = requests.get(url, timeout=config.VIDEO_TIMEOUT)
                dl.raise_for_status()
                Path(out_path).write_bytes(dl.content)
                return out_path
            if t.status == "FAILED":
                raise RuntimeError(f"Runway task failed: {getattr(t, 'failure', '')}")
        raise RuntimeError("Runway task timed out")
