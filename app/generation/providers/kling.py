"""Kling (Kuaishou) image->video adapter.

No first-party Python SDK, so this talks to the REST API directly with a JWT
signed from your access/secret key pair. Set ``KLING_API_KEY`` as
``<access_key>:<secret_key>``. Needs ``pip install pyjwt`` for signing.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path

import requests

from ... import config
from ..base import ClipGenerator, Scene
from ._common import full_prompt, scene_image

_BASE = "https://api.klingai.com"
_MODEL = "kling-v1-6"


class KlingClipGenerator(ClipGenerator):
    name = "kling (Kuaishou)"

    def available(self) -> tuple[bool, str]:
        if not config.KLING_API_KEY or ":" not in config.KLING_API_KEY:
            return False, "KLING_API_KEY not set as '<access_key>:<secret_key>'"
        return True, f"configured ({_MODEL})"

    def _token(self) -> str:
        try:
            import jwt  # optional dependency (pyjwt)
        except ImportError as exc:  # noqa: BLE001
            raise RuntimeError("kling backend needs `pip install pyjwt`") from exc
        ak, sk = config.KLING_API_KEY.split(":", 1)
        now = int(time.time())
        return jwt.encode(
            {"iss": ak, "exp": now + 1800, "nbf": now - 5},
            sk, algorithm="HS256", headers={"alg": "HS256", "typ": "JWT"},
        )

    def generate_clip(self, scene: Scene, out_path: str, variant: int = 0) -> str:
        ok, why = self.available()
        if not ok:
            raise RuntimeError(why)
        img = scene_image(scene, Path(out_path).parent)
        b64 = base64.b64encode(Path(img).read_bytes()).decode("ascii")
        headers = {"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json"}
        body = {
            "model_name": _MODEL,
            "image": b64,
            "prompt": full_prompt(scene),
            "negative_prompt": config.COSMOS_NEGATIVE_PROMPT,
            "mode": "std",
            "duration": "5",
        }
        resp = requests.post(f"{_BASE}/v1/videos/image2video", json=body, headers=headers,
                             timeout=config.VIDEO_TIMEOUT)
        resp.raise_for_status()
        task_id = resp.json()["data"]["task_id"]

        deadline = time.time() + config.VIDEO_TIMEOUT
        while time.time() < deadline:
            time.sleep(5)
            q = requests.get(f"{_BASE}/v1/videos/image2video/{task_id}",
                             headers={"Authorization": f"Bearer {self._token()}"},
                             timeout=config.VIDEO_TIMEOUT)
            q.raise_for_status()
            data = q.json()["data"]
            status = data.get("task_status")
            if status == "succeed":
                url = data["task_result"]["videos"][0]["url"]
                dl = requests.get(url, timeout=config.VIDEO_TIMEOUT)
                dl.raise_for_status()
                Path(out_path).write_bytes(dl.content)
                return out_path
            if status == "failed":
                raise RuntimeError(f"Kling task failed: {data.get('task_status_msg')}")
        raise RuntimeError("Kling task timed out")
