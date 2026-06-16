"""Shared photo analysis for the videographer skill.

GPT-4o (vision) looks at ONE photo of any space/subject and returns a short,
embodied first-person motion prompt that the video model can film, plus a 2-4
word label and a camera "shot". Use-case agnostic: it works for a rental, a
cafe, a gym, a product table, etc. The brand positioning (from the dossier) is
passed in as ``context`` so the prompt adapts to the venue without hardcoding a
domain.

Both the marketing loop and the manual montage CLI import from here, so there is
one analyzer (and one image encoder) rather than three.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

from . import config
from .ffmpeg_utils import extract_frame

_MAX_IMAGE_PX = 768  # longest side; keeps vision tokens (and cost) modest

# Generic, domain-agnostic camera moves Cosmos handles well (embodied POV).
GENERIC_SHOTS = ["approach and enter", "walk in", "walk forward", "step inside", "slow arc"]

_VISION_SYSTEM = (
    "You are a cinematographer using NVIDIA Cosmos, a world-model video generator "
    "that excels at embodied, first-person forward motion through real spaces. "
    "Look at ONE photo of a space or subject and return STRICT JSON: "
    '{"label": "2-4 word name of what this is", '
    '"shot": one of [walk in, walk forward, step inside, approach and enter, '
    'dolly-in with parallax, slow arc], '
    '"prompt": "a short (1-2 sentence) image-to-video instruction for a smooth, '
    'gimbal-stabilized FIRST-PERSON POV that moves forward into THIS exact scene '
    "at a natural pace, with real parallax and depth. Keep the scene identical to "
    "the photo; no people in frame (the viewer is the camera); natural light.\"}"
)


def encode_image(path: str, work: Path, idx: int) -> str | None:
    """Return a base64 data URI for an image (or a video's first frame), or None."""
    try:
        from PIL import Image

        src = path
        if Path(path).suffix.lower() in config.ALLOWED_VIDEO_EXTS:
            src = extract_frame(path, str(work / f"vis_frame_{idx}.png"), at_seconds=1.0)

        with Image.open(src) as im:
            im = im.convert("RGB")
            im.thumbnail((_MAX_IMAGE_PX, _MAX_IMAGE_PX))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=85)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{b64}"
    except Exception as exc:  # noqa: BLE001
        print(f"[vision] could not encode image {path}: {exc}")
        return None


def analyze(path: str, work: Path, idx: int, *, use_vision: bool = True, context: str = "") -> dict:
    """Return {label, shot, prompt} for a photo (GPT-4o vision or a fallback).

    ``context`` is an optional brand/use-case hint (e.g. the dossier positioning)
    that nudges the prompt toward the right vibe without hardcoding a domain.
    """
    fallback = {
        "label": f"Scene {idx + 1}",
        "shot": GENERIC_SHOTS[idx % len(GENERIC_SHOTS)],
        "prompt": (
            "Smooth, gimbal-stabilized first-person POV that moves forward into "
            "this exact scene at a natural pace, real parallax and depth. Keep it "
            "identical to the photo; no people in frame; natural light."
        ),
    }
    if not use_vision or not config.OPENAI_API_KEY:
        return fallback
    uri = encode_image(path, work, idx)
    if not uri:
        return fallback
    try:
        from openai import OpenAI

        client = OpenAI(api_key=config.OPENAI_API_KEY)
        user_text = "Look at this scene and return the JSON."
        if context:
            user_text = f"Brand/use-case context: {context}\n\n{user_text}"
        resp = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            response_format={"type": "json_object"},
            temperature=0.5,
            max_tokens=300,
            messages=[
                {"role": "system", "content": _VISION_SYSTEM},
                {"role": "user", "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": uri, "detail": "low"}},
                ]},
            ],
        )
        import json as _json

        data = _json.loads(resp.choices[0].message.content or "{}")
        return {
            "label": str(data.get("label") or fallback["label"])[:40],
            "shot": str(data.get("shot") or fallback["shot"]),
            "prompt": str(data.get("prompt") or fallback["prompt"]),
        }
    except Exception as exc:  # noqa: BLE001
        print(f"[vision] analysis failed ({exc}); using a generic walk-in")
        return fallback
