"""Generic OpenAI-compatible image->video backend (bring-your-own-model).

Speaks the same vLLM-Omni-style multipart ``/videos`` API the Cosmos backend
uses, but driven entirely by the ``VIDEO_*`` env vars so you can point it at any
self-hosted or hosted model without writing a new adapter:

    LIVEHERE_BACKEND=openai_video
    VIDEO_BASE_URL=https://my-model.example.com/v1
    VIDEO_API_KEY=sk-...
    VIDEO_MODEL=my-org/My-I2V-Model
    VIDEO_SIZE=1280x720
    # optional, merged into the request as form fields:
    VIDEO_EXTRA_PARAMS={"guidance_scale": 5.0}

It sends the scene's reference photo + prompt and writes the returned MP4 (or
follows a URL in the JSON response). The number of frames comes from the shared
``COSMOS_NUM_FRAMES`` knob the videographer sets per cut.
"""

from __future__ import annotations

import json
import mimetypes
import time
from pathlib import Path

import requests

from .. import config
from ..ffmpeg_utils import extract_frame
from .base import ClipGenerator, Scene


class OpenAICompatibleVideoClipGenerator(ClipGenerator):
    name = "openai_video (generic image->video)"

    def available(self) -> tuple[bool, str]:
        if not config.VIDEO_BASE_URL:
            return False, "VIDEO_BASE_URL not set"
        return True, f"configured ({config.VIDEO_MODEL or 'default model'} @ {config.VIDEO_BASE_URL})"

    def live(self, timeout: float = 8.0) -> bool:
        ok, _ = self.available()
        if not ok:
            return False
        try:
            r = requests.get(config.VIDEO_BASE_URL.rstrip("/") + "/models", timeout=timeout)
            return 200 <= r.status_code < 300
        except Exception:  # noqa: BLE001
            return False

    def _prompt(self, scene: Scene) -> str:
        return scene.prompt.rstrip() + config.COSMOS_FIDELITY_SUFFIX

    def generate_clip(self, scene: Scene, out_path: str, variant: int = 0) -> str:
        if not scene.source_path:
            raise ValueError("openai_video backend requires a source image for the scene")
        ok, why = self.available()
        if not ok:
            raise RuntimeError(why)

        work = Path(out_path).parent
        src = scene.source_path
        if Path(src).suffix.lower() in config.ALLOWED_VIDEO_EXTS:
            src = extract_frame(src, str(work / f"frame_{scene.index}.png"), at_seconds=1.0)
        mime = mimetypes.guess_type(src)[0] or "image/png"

        data = {
            "prompt": self._prompt(scene),
            "negative_prompt": config.COSMOS_NEGATIVE_PROMPT,
            "size": config.VIDEO_SIZE,
            "num_frames": str(config.COSMOS_NUM_FRAMES),
            "fps": str(config.COSMOS_FPS),
            "num_inference_steps": str(config.COSMOS_STEPS),
            "guidance_scale": str(config.COSMOS_GUIDANCE),
            "seed": str(1000 + scene.index * 100 + variant * 17),
        }
        if config.VIDEO_MODEL:
            data["model"] = config.VIDEO_MODEL
        if config.VIDEO_EXTRA_PARAMS:
            try:
                data.update({k: str(v) for k, v in json.loads(config.VIDEO_EXTRA_PARAMS).items()})
            except Exception as exc:  # noqa: BLE001
                print(f"[openai_video] ignoring bad VIDEO_EXTRA_PARAMS ({exc})")

        headers = {"Accept": "video/mp4"}
        if config.VIDEO_API_KEY:
            headers["Authorization"] = f"Bearer {config.VIDEO_API_KEY}"
        url = config.VIDEO_BASE_URL + config.VIDEO_VIDEOS_PATH

        last_err: Exception | None = None
        for attempt in range(1, config.VIDEO_MAX_RETRIES + 1):
            try:
                with open(src, "rb") as image_file:
                    files = {"input_reference": (Path(src).name, image_file, mime)}
                    resp = requests.post(url, data=data, files=files, headers=headers,
                                         timeout=config.VIDEO_TIMEOUT)
                if resp.status_code >= 400:
                    raise RuntimeError(f"video request failed ({resp.status_code}): {resp.text[:400]}")
                content = resp.content
                if "video" in resp.headers.get("content-type", "") or content[:4] in (
                    b"\x00\x00\x00\x18", b"\x00\x00\x00\x1c",
                ):
                    Path(out_path).write_bytes(content)
                    return out_path
                payload = resp.json()
                url2 = _find_url(payload)
                if not url2:
                    raise RuntimeError(f"no video in response: {str(payload)[:400]}")
                dl = requests.get(url2, timeout=config.VIDEO_TIMEOUT)
                dl.raise_for_status()
                Path(out_path).write_bytes(dl.content)
                return out_path
            except (requests.exceptions.RequestException, RuntimeError) as e:
                last_err = e
                if attempt < config.VIDEO_MAX_RETRIES:
                    print(f"[openai_video] clip {scene.index} attempt {attempt} failed ({type(e).__name__}); retrying…")
                    time.sleep(3)
        raise RuntimeError(f"openai_video clip {scene.index} failed after retries: {last_err}")


def _find_url(payload):
    if isinstance(payload, dict):
        for v in payload.values():
            if isinstance(v, str) and v.startswith("http"):
                return v
            found = _find_url(v)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_url(item)
            if found:
                return found
    return None
