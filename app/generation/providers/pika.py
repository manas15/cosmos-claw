"""Pika Labs image->video adapter (REST).

No first-party Python SDK, so this calls the Pika HTTP API directly. Set
``PIKA_API_KEY``. Endpoint/field names track Pika's image-to-video route; adjust
``_BASE``/``_MODEL`` to your plan if Pika revs the API.
"""

from __future__ import annotations

import time
from pathlib import Path

import requests

from ... import config
from ..base import ClipGenerator, Scene
from ._common import full_prompt, scene_image

_BASE = "https://api.pika.art"
_MODEL = "pika-2.2"


class PikaClipGenerator(ClipGenerator):
    name = "pika (Pika Labs)"

    def available(self) -> tuple[bool, str]:
        if not config.PIKA_API_KEY:
            return False, "PIKA_API_KEY not set"
        return True, f"configured ({_MODEL})"

    def generate_clip(self, scene: Scene, out_path: str, variant: int = 0) -> str:
        ok, why = self.available()
        if not ok:
            raise RuntimeError(why)
        img = scene_image(scene, Path(out_path).parent)
        headers = {"Authorization": f"Bearer {config.PIKA_API_KEY}"}
        with open(img, "rb") as f:
            files = {"image": (Path(img).name, f, "image/png")}
            data = {"model": _MODEL, "promptText": full_prompt(scene),
                    "negativePrompt": config.COSMOS_NEGATIVE_PROMPT, "aspectRatio": "9:16"}
            resp = requests.post(f"{_BASE}/generate/image-to-video", data=data, files=files,
                                 headers=headers, timeout=config.VIDEO_TIMEOUT)
        resp.raise_for_status()
        job_id = resp.json()["id"]

        deadline = time.time() + config.VIDEO_TIMEOUT
        while time.time() < deadline:
            time.sleep(5)
            q = requests.get(f"{_BASE}/jobs/{job_id}", headers=headers, timeout=config.VIDEO_TIMEOUT)
            q.raise_for_status()
            data = q.json()
            if data.get("status") == "finished":
                url = data["videoUrl"]
                dl = requests.get(url, timeout=config.VIDEO_TIMEOUT)
                dl.raise_for_status()
                Path(out_path).write_bytes(dl.content)
                return out_path
            if data.get("status") in ("failed", "error"):
                raise RuntimeError(f"Pika job failed: {data.get('error')}")
        raise RuntimeError("Pika job timed out")
