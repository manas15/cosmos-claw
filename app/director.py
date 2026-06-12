"""GPT-4o "director": turn raw uploads into a first-person POV storyboard.

Given the user's photos/videos of a unit plus a free-text brief, GPT-4o (vision)
writes a short day-in-the-life shot list: ~7 beats from morning to evening, each
anchored to the best-matching uploaded room, with a first-person POV Cosmos
prompt, an on-screen caption, a clock label, and a continuity flag.

If anything goes wrong (no API key, network error, bad JSON) we fall back to the
simple morning/day/evening storyboard in ``storyboard.py`` so the app still works.
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

from . import config
from .ffmpeg_utils import extract_frame
from .generation.base import Scene
from .storyboard import build_storyboard

_MAX_IMAGE_PX = 768  # longest side; keeps vision tokens (and cost) modest

_TIME_OF_DAY_ALIASES = {
    "dawn": "morning",
    "sunrise": "morning",
    "morning": "morning",
    "midmorning": "morning",
    "noon": "day",
    "midday": "day",
    "day": "day",
    "afternoon": "day",
    "evening": "evening",
    "dusk": "evening",
    "sunset": "evening",
    "golden": "evening",
    "golden hour": "evening",
    "night": "evening",
}

_SYSTEM_PROMPT = """\
You are the director for "LiveHere", which turns a few photos of a home into a \
cinematic, first-person POV "day in the life" video that helps a prospective \
renter feel what living there is like.

You will receive one or more images of the unit (each labeled "Image index N") \
and a short brief from the lister. Plan a sequence of beats that flows from \
morning to evening through the actual rooms shown.

Hard rules:
- FIRST-PERSON POV only. The camera IS the viewer's eyes. NEVER describe a \
visible person, character, face, or body (hands/feet briefly are fine). No \
third-person subjects.
- Every beat must reference exactly one provided image by its integer index \
("room_image"); only use indices that exist.
- Each "prompt" is a vivid image-to-video instruction: the POV action, a slow \
deliberate camera move (e.g. gentle push-in, pan, tilt up), and lighting that \
matches the time of day. Keep it photorealistic. No on-screen text, captions, \
people, or watermarks in the prompt.
- "continues_prev" is true ONLY when this beat stays in the SAME room as the \
previous beat (so the motion can flow continuously); use false when changing \
rooms.
- "caption" is a short on-screen lower-third (max 6 words), warm and human.
- "time_label" is a clock time like "7:00 AM"; "time_of_day" is one of exactly: \
morning, day, evening.

Return STRICT JSON only, matching this shape:
{
  "title": "string",
  "beats": [
    {
      "room_image": 0,
      "time_label": "7:00 AM",
      "time_of_day": "morning",
      "prompt": "First-person POV ...",
      "caption": "Wake up to soft light",
      "continues_prev": false
    }
  ]
}
"""


def _encode_image(path: str, work: Path, idx: int) -> str | None:
    """Return a base64 data URI for an uploaded image/video frame, or None."""
    try:
        from PIL import Image

        src = path
        if Path(path).suffix.lower() in config.ALLOWED_VIDEO_EXTS:
            src = extract_frame(path, str(work / f"dir_frame_{idx}.png"), at_seconds=1.0)

        with Image.open(src) as im:
            im = im.convert("RGB")
            im.thumbnail((_MAX_IMAGE_PX, _MAX_IMAGE_PX))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=85)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{b64}"
    except Exception as exc:  # noqa: BLE001
        print(f"[director] could not encode image {path}: {exc}")
        return None


def _normalize_time_of_day(value: str) -> str:
    key = (value or "").strip().lower()
    return _TIME_OF_DAY_ALIASES.get(key, "day")


def build_director_storyboard(
    media_paths: list[str],
    instructions: str,
    address: str = "",
    lease: str = "",
    *,
    work_dir: Path | None = None,
) -> list[Scene]:
    """Plan the storyboard with GPT-4o. Falls back to the simple plan on error."""
    if not config.OPENAI_API_KEY:
        print("[director] OPENAI_API_KEY not set -> using simple storyboard")
        return build_storyboard(media_paths, instructions)
    if not media_paths:
        return build_storyboard(media_paths, instructions)

    work = work_dir or Path(media_paths[0]).parent
    work.mkdir(parents=True, exist_ok=True)

    encoded: list[str] = []
    valid_indices: list[int] = []
    for i, path in enumerate(media_paths):
        uri = _encode_image(path, work, i)
        if uri:
            encoded.append(uri)
            valid_indices.append(i)
    if not encoded:
        return build_storyboard(media_paths, instructions)

    brief = instructions.strip() or "A welcoming home; show what a calm day here feels like."
    context = f"Brief: {brief}"
    if address:
        context += f"\nLocation: {address}"
    if lease:
        context += f"\nLease: {lease}"
    context += (
        f"\n\nThere are {len(encoded)} images, indices 0..{len(encoded) - 1}. "
        f"Plan about {config.DIRECTOR_TARGET_BEATS} beats from morning to evening, "
        f"reusing rooms as needed."
    )

    user_content: list[dict] = [{"type": "text", "text": context}]
    for n, uri in enumerate(encoded):
        user_content.append({"type": "text", "text": f"Image index {n}:"})
        user_content.append({"type": "image_url", "image_url": {"url": uri, "detail": "low"}})

    try:
        from openai import OpenAI

        client = OpenAI(api_key=config.OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            response_format={"type": "json_object"},
            temperature=0.7,
            max_tokens=1600,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )
        raw = resp.choices[0].message.content or "{}"
        data = json.loads(raw)
        beats = data.get("beats") or []
        if not isinstance(beats, list) or not beats:
            raise ValueError("director returned no beats")
    except Exception as exc:  # noqa: BLE001
        print(f"[director] GPT-4o planning failed ({exc}); using simple storyboard")
        return build_storyboard(media_paths, instructions)

    scenes: list[Scene] = []
    for i, beat in enumerate(beats):
        if not isinstance(beat, dict):
            continue
        # Map the model's 0-based image index back to a real media path (clamped).
        try:
            img_n = int(beat.get("room_image", 0))
        except (TypeError, ValueError):
            img_n = 0
        img_n = max(0, min(img_n, len(encoded) - 1))
        source_path = media_paths[valid_indices[img_n]]

        prompt = str(beat.get("prompt") or "").strip()
        if not prompt:
            continue
        caption = str(beat.get("caption") or "").strip()[:90]
        time_label = str(beat.get("time_label") or "").strip() or "Daytime"
        tod = _normalize_time_of_day(str(beat.get("time_of_day") or "day"))
        continues_prev = bool(beat.get("continues_prev", False)) and i > 0

        scenes.append(
            Scene(
                index=i,
                source_path=source_path,
                prompt=prompt,
                caption=caption,
                time_label=time_label,
                time_of_day=tod,
                duration=config.SCENE_DURATION,
                continues_prev=continues_prev,
            )
        )

    if not scenes:
        return build_storyboard(media_paths, instructions)

    print(f"[director] planned {len(scenes)} beats with {config.OPENAI_MODEL}")
    return scenes
