"""NVIDIA Cosmos 3 clip generator (image -> video).

Supports two API styles (auto-detected from the base URL, override with
COSMOS_API_STYLE):

  * "nvidia_infer"  — NVIDIA-hosted free endpoint on build.nvidia.com.
        POST {base}{infer_path}  with JSON {prompt, image(base64 data URI),
        resolution, num_output_frames, steps, guidance_scale, seed}.
        Returns JSON {"b64_video": "<base64 mp4>"} (or a 202 + NVCF polling).

  * "vllm_omni"     — any self-hosted vLLM-Omni server (RunPod/Modal/Nebius).
        multipart POST {base}/videos/sync with an `input_reference` image,
        returns the MP4 bytes directly.

Captions/grading/fades are NOT done here; this returns a raw motion clip and the
videographer cuts and voices it afterward (same contract as the local stub).
"""

from __future__ import annotations

import base64
import mimetypes
import time
from pathlib import Path

import requests

from .. import config
from ..ffmpeg_utils import extract_frame
from .base import ClipGenerator, Scene


class CosmosClipGenerator(ClipGenerator):
    name = "cosmos (NVIDIA Cosmos 3)"

    def __init__(self) -> None:
        self.style = _resolve_style(config.COSMOS_API_STYLE, config.COSMOS_BASE_URL)

    def available(self) -> tuple[bool, str]:
        if not config.COSMOS_BASE_URL:
            return False, "COSMOS_BASE_URL not set"
        if not config.COSMOS_API_KEY:
            return False, "COSMOS_API_KEY not set"
        return True, f"configured ({self.style} @ {config.COSMOS_BASE_URL})"

    def live(self, timeout: float = 8.0) -> bool:
        """Real liveness probe (GET {base}/models) — catches a dropped SSH tunnel
        that the config-only ``available()`` cannot see."""
        ok, _ = self.available()
        if not ok:
            return False
        url = config.COSMOS_BASE_URL.rstrip("/") + "/models"
        try:
            with requests.Session() as s:
                r = s.get(url, timeout=timeout)
            return 200 <= r.status_code < 300
        except Exception:  # noqa: BLE001
            return False

    def generate_clip(self, scene: Scene, out_path: str, variant: int = 0) -> str:
        if not scene.source_path:
            raise ValueError("CosmosClipGenerator requires a source image/video for the scene")

        work = Path(out_path).parent
        src = scene.source_path
        if Path(src).suffix.lower() in config.ALLOWED_VIDEO_EXTS:
            src = extract_frame(src, str(work / f"frame_{scene.index}.png"), at_seconds=1.0)

        if self.style == "nvidia_infer":
            return self._generate_nvidia_infer(scene, src, out_path, variant)
        return self._generate_vllm_omni(scene, src, out_path, variant)

    @staticmethod
    def _fidelity_prompt(scene: Scene) -> str:
        """Anchor the prompt to the real photo so Cosmos doesn't hallucinate."""
        return scene.prompt.rstrip() + config.COSMOS_FIDELITY_SUFFIX

    @staticmethod
    def _seed(scene: Scene, variant: int) -> int:
        """Distinct, reproducible seed per beat + take."""
        return 1000 + scene.index * 100 + variant * 17

    @staticmethod
    def _flow_shift(scene: Scene, variant: int) -> float:
        """Map the beat's motion_strength to flow_shift (bolder move -> more motion).

        A small per-variant jitter spreads best-of-N takes across the motion range.
        """
        s = min(1.0, max(0.0, float(scene.motion_strength or 0.5)))
        base = config.COSMOS_FLOW_SHIFT * (1.0 + config.COSMOS_MOTION_FLOW_GAIN * (s - 0.5))
        jitter = (0.0, 0.12, -0.12, 0.24)[variant % 4] * config.COSMOS_FLOW_SHIFT
        val = base + jitter
        return round(max(config.COSMOS_FLOW_SHIFT_MIN, min(config.COSMOS_FLOW_SHIFT_MAX, val)), 3)

    # --- NVIDIA-hosted build.nvidia.com "infer" JSON API -------------------
    def _generate_nvidia_infer(self, scene: Scene, src: str, out_path: str, variant: int = 0) -> str:
        mime = mimetypes.guess_type(src)[0] or "image/png"
        b64 = base64.b64encode(Path(src).read_bytes()).decode("ascii")
        body = {
            "prompt": self._fidelity_prompt(scene),
            "image": f"data:{mime};base64,{b64}",
            "negative_prompt": config.COSMOS_NEGATIVE_PROMPT,
            "resolution": config.COSMOS_RESOLUTION,
            "num_output_frames": config.COSMOS_NUM_FRAMES,
            "steps": config.COSMOS_STEPS,
            "guidance_scale": config.COSMOS_GUIDANCE,
            "seed": self._seed(scene, variant),
        }
        headers = {
            "Authorization": f"Bearer {config.COSMOS_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        url = config.COSMOS_BASE_URL + config.COSMOS_INFER_PATH
        resp = requests.post(url, json=body, headers=headers, timeout=config.COSMOS_TIMEOUT)

        # Long-running NVCF jobs return 202 + a request id to poll.
        if resp.status_code == 202:
            resp = _poll_nvcf(resp, headers)

        if resp.status_code >= 400:
            raise RuntimeError(
                f"Cosmos infer failed ({resp.status_code}) at {url}: {resp.text[:400]}"
            )

        payload = resp.json()
        b64_video = _find_key(payload, ("b64_video", "video", "b64_json"))
        if not b64_video:
            url2 = _find_url(payload)
            if url2:
                dl = requests.get(url2, timeout=config.COSMOS_TIMEOUT)
                dl.raise_for_status()
                Path(out_path).write_bytes(dl.content)
                return out_path
            raise RuntimeError(f"No video in Cosmos response: {str(payload)[:400]}")

        Path(out_path).write_bytes(base64.b64decode(b64_video))
        return out_path

    # --- self-hosted vLLM-Omni multipart /videos/sync API ------------------
    def _generate_vllm_omni(self, scene: Scene, src: str, out_path: str, variant: int = 0) -> str:
        mime = mimetypes.guess_type(src)[0] or "image/png"
        data = {
            "model": config.COSMOS_MODEL,
            "prompt": self._fidelity_prompt(scene),
            "negative_prompt": config.COSMOS_NEGATIVE_PROMPT,
            "size": config.COSMOS_SIZE,
            "num_frames": str(config.COSMOS_NUM_FRAMES),
            "fps": str(config.COSMOS_FPS),
            "num_inference_steps": str(config.COSMOS_STEPS),
            "guidance_scale": str(config.COSMOS_GUIDANCE),
            "max_sequence_length": "4096",
            "flow_shift": str(self._flow_shift(scene, variant)),
            "extra_params": (
                '{"use_resolution_template":false,'
                '"use_duration_template":false,"guardrails":true}'
            ),
            "seed": str(self._seed(scene, variant)),
        }
        headers = {
            "Authorization": f"Bearer {config.COSMOS_API_KEY}",
            "Accept": "video/mp4",
        }
        url = config.COSMOS_BASE_URL + config.COSMOS_VIDEOS_PATH

        # The SSH tunnel can blip during a multi-minute job, truncating the MP4
        # download. Retry the whole clip a few times so one hiccup doesn't fail
        # the entire video.
        last_err: Exception | None = None
        for attempt in range(1, config.COSMOS_MAX_RETRIES + 1):
            try:
                with open(src, "rb") as image_file:
                    files = {"input_reference": (Path(src).name, image_file, mime)}
                    resp = requests.post(
                        url, data=data, files=files, headers=headers,
                        timeout=config.COSMOS_TIMEOUT,
                    )
                if resp.status_code >= 400:
                    raise RuntimeError(
                        f"Cosmos request failed ({resp.status_code}): {resp.text[:400]}"
                    )

                content = resp.content  # forces full read; raises on truncation
                if "video" in resp.headers.get("content-type", "") or content[:4] in (
                    b"\x00\x00\x00\x18",
                    b"\x00\x00\x00\x1c",
                ):
                    Path(out_path).write_bytes(content)
                    return out_path

                payload = resp.json()
                url2 = _find_url(payload)
                if not url2:
                    raise RuntimeError(f"No video URL in Cosmos response: {str(payload)[:400]}")
                dl = requests.get(url2, timeout=config.COSMOS_TIMEOUT)
                dl.raise_for_status()
                Path(out_path).write_bytes(dl.content)
                return out_path
            except (requests.exceptions.RequestException, RuntimeError) as e:
                last_err = e
                if attempt < config.COSMOS_MAX_RETRIES:
                    print(
                        f"[cosmos] clip {scene.index} attempt {attempt} failed "
                        f"({type(e).__name__}); retrying…"
                    )
                    time.sleep(3)

        raise RuntimeError(f"Cosmos clip {scene.index} failed after retries: {last_err}")


def _resolve_style(style: str, base_url: str) -> str:
    style = (style or "auto").lower()
    if style != "auto":
        return style
    return "nvidia_infer" if "ai.api.nvidia.com" in base_url else "vllm_omni"


def _poll_nvcf(resp: requests.Response, headers: dict, max_wait: int = 600) -> requests.Response:
    """Poll an NVCF status endpoint until the job completes."""
    req_id = resp.headers.get("NVCF-REQID") or resp.json().get("reqId")
    if not req_id:
        return resp
    status_url = f"https://ai.api.nvidia.com/v1/status/{req_id}"
    deadline = time.time() + max_wait
    while time.time() < deadline:
        time.sleep(3)
        r = requests.get(status_url, headers=headers, timeout=60)
        if r.status_code != 202:
            return r
    raise RuntimeError("Cosmos NVCF job timed out while polling status.")


def _find_key(payload, keys):
    if isinstance(payload, dict):
        for k in keys:
            v = payload.get(k)
            if isinstance(v, str) and len(v) > 100:
                return v
        for v in payload.values():
            found = _find_key(v, keys)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_key(item, keys)
            if found:
                return found
    return None


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
