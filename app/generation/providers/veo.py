"""Google Veo image->video adapter.

Optional dependency: ``pip install google-genai``. Set ``GOOGLE_API_KEY``.
Reference implementation against the ``google.genai`` ``generate_videos`` API
with an input image (poll the long-running operation).
"""

from __future__ import annotations

import time
from pathlib import Path

from ... import config
from ..base import ClipGenerator, Scene
from ._common import full_prompt, scene_image

_MODEL = "veo-3.0-generate-001"


class VeoClipGenerator(ClipGenerator):
    name = "veo (Google)"

    def available(self) -> tuple[bool, str]:
        if not config.GOOGLE_API_KEY:
            return False, "GOOGLE_API_KEY not set"
        return True, f"configured ({_MODEL})"

    def generate_clip(self, scene: Scene, out_path: str, variant: int = 0) -> str:
        ok, why = self.available()
        if not ok:
            raise RuntimeError(why)
        try:
            from google import genai  # optional dependency (google-genai)
            from google.genai import types
        except ImportError as exc:  # noqa: BLE001
            raise RuntimeError("veo backend needs `pip install google-genai`") from exc

        img = scene_image(scene, Path(out_path).parent)
        client = genai.Client(api_key=config.GOOGLE_API_KEY)
        op = client.models.generate_videos(
            model=_MODEL,
            prompt=full_prompt(scene),
            image=types.Image.from_file(location=img),
            config=types.GenerateVideosConfig(aspect_ratio="9:16", number_of_videos=1),
        )
        deadline = time.time() + config.VIDEO_TIMEOUT
        while time.time() < deadline:
            if op.done:
                video = op.response.generated_videos[0].video
                client.files.download(file=video)
                video.save(out_path)
                return out_path
            time.sleep(8)
            op = client.operations.get(op)
        raise RuntimeError("Veo operation timed out")
