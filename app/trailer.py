"""GPT-4o "director" for the vertical listing trailer.

Given a property's photos plus listing facts (name, location, price, rating,
amenities), GPT-4o (vision) acts as a real-estate videographer + copywriter:

  * picks the most flattering, distinct shots and orders them like a tour,
  * assigns each a SUBTLE, controlled camera move (no people, no invented
    actions) so Cosmos stays faithful to the real photo,
  * writes a short on-screen selling-point caption per beat,
  * writes a ~30s voiceover script grounded only in the provided facts,
  * fills an end card (name / location / highlight / CTA).

Falls back to a simple heuristic plan if there's no API key or anything fails,
so the app still produces a trailer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .director import _encode_image
from .generation.base import Scene

# Camera moves we allow Cosmos to perform. Anything outside this set tends to
# make the model hallucinate; these keep the room photoreal.
_ALLOWED_SHOTS = {
    "slow push-in",
    "slow pull-back",
    "gentle pan left",
    "gentle pan right",
    "slow tilt up",
    "parallax drift",
}

_SYSTEM_PROMPT = """\
You are the director and copywriter for "LiveHere", which turns a property \
host's photos + listing facts into a polished ~30 second VERTICAL (9:16) listing \
trailer that makes viewers want to book.

You receive several images of ONE property (each labeled "Image index N") and \
listing facts as text. Some facts may be missing — NEVER invent specifics \
(price, ratings, distances, amenities) that are not provided.

Plan a sequence of cinematic beats that tours the property like a real-estate \
videographer: a strong opening exterior/approach shot, then the best interior \
and outdoor spaces, ending on a signature shot. Use only the most flattering, \
distinct images; skip near-duplicates and unflattering shots.

Hard rules for EACH beat:
- "room_image": exactly one existing image index (integer).
- "shot": exactly one of: slow push-in, slow pull-back, gentle pan left, \
gentle pan right, slow tilt up, parallax drift.
- "prompt": an image-to-video instruction for that EXACT room performing the \
chosen move VERY slowly and subtly (a small, almost imperceptible motion). \
It must read as REAL live-action footage shot on a real camera — never a video \
game, CGI, 3D render, or animation. The room must stay 100% identical to the \
photo — same walls, furniture, objects, colors, materials, and lighting. \
ABSOLUTELY no people, no added/removed/moving objects, no actions, no \
text/watermark, no changing layout. Describe only the real space you see and the \
gentle camera move. Keep it short.
- "caption": a short on-screen selling point (max 5 words), Title Case, drawn \
from the facts or the visible space (e.g. "Private Garden Patio", "Walk To \
Runyon Canyon", "Bright Mediterranean Charm"). No emojis.

Also produce:
- "title": a 2-4 word property name/hook for the opening.
- "voiceover": a warm, natural ~75 word script (about 30 seconds spoken) that \
narrates the tour AND the location story — weave in 1-2 nearby highlights \
(e.g. a park, transit, dining) and the nightly price IF it is provided. End with \
a soft call to action. Use ONLY provided facts; never invent a price/rating.
- "end_card": { "name", "location", "highlight", "cta" } using provided facts; \
set any unknown field to "".
- "location_query": a GEOCODABLE area for a map — the neighborhood/area + city + \
state/country (e.g. "Hollywood, Los Angeles, California"). Never invent a street \
address; use the public area only.
- "price": the nightly price EXACTLY as stated in the facts (e.g. "$239 / night") \
or "" if not present. Never guess.
- "rating": {"score": "4.98", "reviews": "288", "superhost": true, "note": "Top \
5% of homes"} — include only sub-fields supported by the facts; else {}.
- "pois": up to 5 real nearby places (prefer ones named in the facts, else \
well-known spots for that area). Each: {"name": "...", "category": "park|food|\
transit|shopping|cafe", "note": "travel time if known, e.g. 15 min walk"}.

Aim for about {N} room beats. Return STRICT JSON only:
{
  "title": "string",
  "voiceover": "string",
  "beats": [
    {"room_image": 0, "shot": "slow push-in", "prompt": "...", "caption": "..."}
  ],
  "end_card": {"name": "...", "location": "...", "highlight": "...", "cta": "..."},
  "location_query": "Neighborhood, City, State",
  "price": "$239 / night",
  "rating": {"score": "4.98", "reviews": "288", "superhost": true, "note": "..."},
  "pois": [{"name": "...", "category": "park", "note": "15 min walk"}]
}
"""

_SHOT_HINT = {
    "slow push-in": "the camera very slowly and smoothly pushes in a little toward the space",
    "slow pull-back": "the camera very slowly pulls back a little to reveal the space",
    "gentle pan left": "the camera gently and slowly pans a little to the left",
    "gentle pan right": "the camera gently and slowly pans a little to the right",
    "slow tilt up": "the camera slowly tilts upward a little across the space",
    "parallax drift": "the camera drifts sideways a little with a gentle parallax",
}


@dataclass
class TrailerItem:
    """One beat in the trailer: either a room clip or an info card."""

    kind: str  # "room" | "info"
    scene: Scene | None = None
    title: str | None = None  # opening title pill (room beats only)
    info_type: str = ""  # map | neighborhood | price | rating
    data: dict = field(default_factory=dict)


@dataclass
class TrailerPlan:
    """A full vertical-trailer plan: interleaved items + narration + end card."""

    title: str
    scenes: list[Scene]  # room scenes only (kept for metadata)
    items: list[TrailerItem] = field(default_factory=list)
    voiceover: str = ""
    end_card: dict = field(default_factory=dict)
    location: str = ""
    price: str = ""


def _assemble_items(title: str, scenes: list[Scene], info: dict) -> list[TrailerItem]:
    """Interleave room scenes with available info cards (rooms stay the majority).

    Info beats are dropped in after the 2nd / 4th / last room so the location
    map, rating, and price punctuate the tour rather than bunching up.
    """
    info_queue: list[TrailerItem] = []
    if info.get("map"):
        info_queue.append(TrailerItem(kind="info", info_type="map", data=info["map"]))
    if info.get("rating"):
        info_queue.append(TrailerItem(kind="info", info_type="rating", data=info["rating"]))
    if info.get("price"):
        info_queue.append(TrailerItem(kind="info", info_type="price", data=info["price"]))

    n = len(scenes)
    insert_after = {1, 3, n - 1}  # 0-based room indices
    items: list[TrailerItem] = []
    qi = 0
    for i, sc in enumerate(scenes):
        items.append(TrailerItem(kind="room", scene=sc, title=title if i == 0 else None))
        if i in insert_after and qi < len(info_queue):
            items.append(info_queue[qi])
            qi += 1
    while qi < len(info_queue):  # flush any leftovers before the end card
        items.append(info_queue[qi])
        qi += 1
    return items


def _clean_shot(value: str) -> str:
    v = (value or "").strip().lower()
    return v if v in _ALLOWED_SHOTS else "slow push-in"


def _fallback_plan(media_paths: list[str], instructions: str, address: str) -> TrailerPlan:
    """No-LLM plan: tour the photos with alternating subtle moves."""
    shots = [
        "slow push-in",
        "gentle pan right",
        "slow pull-back",
        "gentle pan left",
        "slow tilt up",
        "parallax drift",
    ]
    scenes: list[Scene] = []
    for i, path in enumerate(media_paths[: config.TRAILER_TARGET_BEATS]):
        shot = shots[i % len(shots)]
        scenes.append(
            Scene(
                index=i,
                source_path=path,
                prompt=(
                    f"Photorealistic real-estate b-roll of this space; {_SHOT_HINT[shot]}. "
                    f"Natural daylight, stable architecture, no people, no text."
                ),
                caption="",
                time_label="",
                time_of_day="day",
                duration=config.SCENE_DURATION,
            )
        )
    title = (address or instructions or "Your next stay").strip()[:40]
    items = _assemble_items(title, scenes, {})
    return TrailerPlan(
        title=title, scenes=scenes, items=items, voiceover="", end_card={"name": title}
    )


# What the trailer may contain. Used to gate info cards + steer the director.
ALL_INCLUDES = {"photos", "map", "transit", "amenities", "host", "price"}


def build_trailer_plan(
    media_paths: list[str],
    instructions: str,
    address: str = "",
    lease: str = "",
    *,
    work_dir: Path | None = None,
    include: set[str] | None = None,
) -> TrailerPlan:
    """Plan the vertical trailer with GPT-4o. Falls back on any error."""
    inc = include if include else set(ALL_INCLUDES)
    if not media_paths:
        return _fallback_plan(media_paths, instructions, address)
    if not config.OPENAI_API_KEY:
        print("[trailer] OPENAI_API_KEY not set -> heuristic plan")
        return _fallback_plan(media_paths, instructions, address)

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
        return _fallback_plan(media_paths, instructions, address)

    facts = instructions.strip() or "A charming property; highlight what makes a stay here special."
    context = f"Listing facts / brief:\n{facts}"
    if address:
        context += f"\nProperty name / location: {address}"
    if lease:
        context += f"\nAvailability / price: {lease}"
    context += (
        f"\n\nThere are {len(encoded)} images, indices 0..{len(encoded) - 1}. "
        f"Plan about {config.TRAILER_TARGET_BEATS} beats."
    )

    emphasis: list[str] = []
    if "transit" in inc:
        emphasis.append("- Prioritize nearby transit & connectivity (metro/train/bus) among the POIs and mention easy commuting in the voiceover.")
    if "amenities" in inc:
        emphasis.append("- Highlight standout amenities (AC, fast Wi-Fi, parking, full kitchen, patio, workspace) in captions and the voiceover.")
    if "host" in inc:
        emphasis.append("- Mention the host (and Superhost status) and guest reviews/rating.")
    if "price" in inc:
        emphasis.append("- Include the nightly price in the voiceover if it is provided.")
    if "map" not in inc:
        emphasis.append("- Keep the focus on the home itself; don't over-emphasize the wider neighborhood.")
    if emphasis:
        context += "\n\nEmphasis (from the creator):\n" + "\n".join(emphasis)

    user_content: list[dict] = [{"type": "text", "text": context}]
    for n, uri in enumerate(encoded):
        user_content.append({"type": "text", "text": f"Image index {n}:"})
        user_content.append({"type": "image_url", "image_url": {"url": uri, "detail": "low"}})

    system_prompt = _SYSTEM_PROMPT.replace("{N}", str(config.TRAILER_TARGET_BEATS))

    try:
        from openai import OpenAI

        client = OpenAI(api_key=config.OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            response_format={"type": "json_object"},
            temperature=0.6,
            max_tokens=1800,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        beats = data.get("beats") or []
        if not isinstance(beats, list) or not beats:
            raise ValueError("trailer director returned no beats")
    except Exception as exc:  # noqa: BLE001
        print(f"[trailer] GPT-4o planning failed ({exc}); using heuristic plan")
        return _fallback_plan(media_paths, instructions, address)

    scenes: list[Scene] = []
    for i, beat in enumerate(beats):
        if not isinstance(beat, dict):
            continue
        try:
            img_n = int(beat.get("room_image", 0))
        except (TypeError, ValueError):
            img_n = 0
        img_n = max(0, min(img_n, len(encoded) - 1))
        source_path = media_paths[valid_indices[img_n]]

        shot = _clean_shot(str(beat.get("shot", "")))
        prompt = str(beat.get("prompt") or "").strip()
        if not prompt:
            prompt = (
                f"Photorealistic real-estate b-roll of this space; {_SHOT_HINT[shot]}. "
                f"Natural daylight, stable architecture, no people, no text."
            )
        caption = str(beat.get("caption") or "").strip()[:40]

        scenes.append(
            Scene(
                index=i,
                source_path=source_path,
                prompt=prompt,
                caption=caption,
                time_label="",
                time_of_day="day",
                duration=config.SCENE_DURATION,
            )
        )

    if not scenes:
        return _fallback_plan(media_paths, instructions, address)

    title = str(data.get("title") or address or "Your next stay").strip()[:48]
    voiceover = str(data.get("voiceover") or "").strip()
    end_card = data.get("end_card") if isinstance(data.get("end_card"), dict) else {}

    # Location / price / rating / POIs power the interleaved info cards.
    location_query = str(data.get("location_query") or end_card.get("location") or "").strip()
    price = str(data.get("price") or "").strip()
    rating = data.get("rating") if isinstance(data.get("rating"), dict) else {}
    pois = [p for p in (data.get("pois") or []) if isinstance(p, dict) and p.get("name")]

    info: dict = {}
    if config.INFO_CARDS_ENABLED:
        if "map" in inc and (location_query or pois):
            info["map"] = {
                "location_query": location_query,
                "pois": pois,
                "title": "Explore the neighborhood",
            }
        if "host" in inc and any(rating.get(k) for k in ("score", "reviews", "superhost", "note")):
            info["rating"] = {"rating": rating}
        if "price" in inc and price:
            info["price"] = {"price": price}

    items = _assemble_items(title, scenes, info)

    print(
        f"[trailer] planned {len(scenes)} room beats + "
        f"{len(items) - len(scenes)} info cards with {config.OPENAI_MODEL}"
    )
    return TrailerPlan(
        title=title, scenes=scenes, items=items, voiceover=voiceover,
        end_card=end_card, location=location_query, price=price,
    )
