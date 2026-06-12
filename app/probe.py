"""Check whether the configured Cosmos endpoint is live.

Run:  .venv/bin/python -m app.probe
Sends a tiny generation request and reports the HTTP status so you can tell
when NVIDIA's hosted cosmos3-nano endpoint goes from 404 (not provisioned) to
live, without running the whole app.
"""

from __future__ import annotations

import sys

import requests

from . import config


def main() -> int:
    if not config.COSMOS_BASE_URL or not config.COSMOS_API_KEY:
        print("COSMOS_BASE_URL / COSMOS_API_KEY not set (check .env).")
        return 2

    style = config.COSMOS_API_STYLE
    headers = {"Authorization": f"Bearer {config.COSMOS_API_KEY}"}

    if style in ("nvidia_infer", "auto") and "ai.api.nvidia.com" in config.COSMOS_BASE_URL:
        url = config.COSMOS_BASE_URL + config.COSMOS_INFER_PATH
        body = {
            "prompt": "a calm sunlit empty living room, gentle camera drift",
            "resolution": "256_16_9",
            "num_output_frames": 25,
            "steps": 10,
            "seed": 7,
        }
        print(f"Probing (nvidia_infer): POST {url}")
        r = requests.post(url, json=body, headers={**headers, "Accept": "application/json"}, timeout=180)
    else:
        url = config.COSMOS_BASE_URL + config.COSMOS_VIDEOS_PATH
        print(f"Probing (vllm_omni): POST {url}")
        r = requests.post(
            url,
            data={"model": config.COSMOS_MODEL, "prompt": "test", "num_frames": "25"},
            headers={**headers, "Accept": "video/mp4"},
            timeout=180,
        )

    print(f"HTTP {r.status_code} | type={r.headers.get('content-type')}")
    if r.status_code == 404:
        print("→ Endpoint not provisioned yet. Try again later.")
        return 1
    if r.status_code in (401, 403):
        print("→ Auth problem: check your COSMOS_API_KEY.")
        return 1
    if r.status_code < 300:
        print("→ LIVE! The endpoint is responding. You can generate with LIVEHERE_BACKEND=cosmos.")
        return 0
    print(f"→ Unexpected: {r.text[:300]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
