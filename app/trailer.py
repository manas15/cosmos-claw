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

# Camera moves the director can call. v2: bolder, deliberate cinematic moves
# (not "imperceptible") while still keeping the room photoreal and stable.
_ALLOWED_SHOTS = {
    "slow push-in",
    "slow pull-back",
    "dolly-in with parallax",
    "slow arc",
    "crane up reveal",
    "track through doorway",
    "rack focus",
    "gentle pan left",
    "gentle pan right",
    "slow tilt up",
    "parallax drift",
    # Embodied first-person locomotion (Walkthrough mode) — Cosmos's world-model
    # strength: an agent physically moving through the space.
    "walk in",
    "step inside",
    "approach and enter",
    "walk forward",
}

# Default boldness (0..1) per move; combined with the beat's "motion" hint.
_SHOT_MOTION = {
    "slow push-in": 0.5,
    "slow pull-back": 0.5,
    "dolly-in with parallax": 0.72,
    "slow arc": 0.7,
    "crane up reveal": 0.75,
    "track through doorway": 0.85,
    "rack focus": 0.45,
    "gentle pan left": 0.5,
    "gentle pan right": 0.5,
    "slow tilt up": 0.5,
    "parallax drift": 0.6,
    "walk in": 0.9,
    "step inside": 0.85,
    "approach and enter": 0.92,
    "walk forward": 0.85,
}

# Embodied moves used by Walkthrough mode (first-person POV locomotion).
_WALKTHROUGH_SHOTS = ["approach and enter", "walk in", "walk forward", "step inside"]

# Appended to the director system prompt in Walkthrough mode. Cosmos is a world
# foundation model built for embodied/physical-AI motion, so we frame every beat
# as a first-person walk-in rather than a b-roll camera move.
_WALKTHROUGH_DIRECTIVE = """

WALKTHROUGH MODE (override the shot guidance above):
- Frame EVERY beat as a FIRST-PERSON POV WALK-IN: the viewer is an embodied \
camera at head height physically entering and moving forward through each space.
- For "shot", use ONLY: approach and enter, walk in, walk forward, step inside. \
Open on an arrival/exterior or entry shot, then walk into each subsequent room.
- Set "motion": "high" for every beat.
- "prompt": describe a smooth, gimbal-stabilized first-person POV that WALKS \
FORWARD into this exact room at a natural human pace, with real parallax and \
depth as the space opens up. Keep the room 100% identical to the photo; no \
people anywhere in frame (the viewer IS the camera). Natural light.
"""

# Director "motion" word -> strength.
_MOTION_WORD = {"low": 0.35, "medium": 0.6, "high": 0.85}

_SYSTEM_PROMPT = """\
You are the director and copywriter for "Cosmos Claw", which turns a property \
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
- "shot": exactly one of: slow push-in, slow pull-back, dolly-in with parallax, \
slow arc, crane up reveal, track through doorway, rack focus, gentle pan left, \
gentle pan right, slow tilt up, parallax drift. Vary the moves across beats and \
favor dynamic ones (dolly-in with parallax, slow arc, crane up reveal, track \
through doorway) on hero shots so the trailer feels cinematic, not static.
- "motion": one of low | medium | high — how bold the camera move is. Use medium \
or high for hero/wide spaces, low for tight or detail shots.
- "ambient": OPTIONAL natural in-scene motion that brings the frame alive WITHOUT \
changing the room — only if it truly fits what's visible (e.g. "sheer curtains \
sway gently", "steam rising from the coffee", "pool water shimmers", "leaves \
flutter outside the window", "candle flame flickers"). Use "" if nothing fits.
- "prompt": an image-to-video instruction for that EXACT space performing the \
chosen move as a SMOOTH, DELIBERATE, CINEMATIC camera movement with real parallax \
and depth (not a tiny imperceptible nudge). It must read as REAL live-action \
footage shot on a professional camera — never a video game, CGI, 3D render, or \
animation. The room stays 100% identical to the photo — same walls, furniture, \
objects, colors, materials, and lighting. ABSOLUTELY no people, no added/removed \
objects, no layout changes, no text/watermark. Describe the real space, the \
camera move, and any ambient motion. Keep it to 1-2 sentences.
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
    {"room_image": 0, "shot": "dolly-in with parallax", "motion": "high", \
"ambient": "sheer curtains sway gently", "prompt": "...", "caption": "..."}
  ],
  "end_card": {"name": "...", "location": "...", "highlight": "...", "cta": "..."},
  "location_query": "Neighborhood, City, State",
  "price": "$239 / night",
  "rating": {"score": "4.98", "reviews": "288", "superhost": true, "note": "..."},
  "pois": [{"name": "...", "category": "park", "note": "15 min walk"}]
}
"""

_SHOT_HINT = {
    "slow push-in": "the camera makes a smooth, deliberate dolly push-in toward the focal point of the space",
    "slow pull-back": "the camera glides backward in a steady pull-out that opens up the whole space",
    "dolly-in with parallax": "the camera dollies forward with clear parallax as the foreground and background separate with real depth",
    "slow arc": "the camera arcs in a slow, smooth orbit around the space, revealing its depth and dimension",
    "crane up reveal": "the camera cranes upward in a smooth, sweeping reveal of the space",
    "track through doorway": "the camera tracks smoothly forward through the doorway, leading the eye into the next space",
    "rack focus": "the camera holds a slow drift while the focus racks smoothly from the foreground to the background",
    "gentle pan left": "the camera pans smoothly to the left, sweeping across the space",
    "gentle pan right": "the camera pans smoothly to the right, sweeping across the space",
    "slow tilt up": "the camera tilts upward in a smooth, revealing sweep across the space",
    "parallax drift": "the camera drifts sideways with a clear, smooth parallax that shows depth",
    "walk in": "the camera walks forward into the room in a smooth, gimbal-stabilized first-person POV at head height, revealing the space with natural parallax",
    "step inside": "the camera steps inside through the threshold and keeps moving forward at a natural walking pace, depth opening up around it",
    "approach and enter": "the camera approaches the entrance and walks in, moving forward into the space at a natural pace",
    "walk forward": "the camera walks steadily forward through the space at a natural human pace, parallax revealing the depth of the room",
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


def _motion_strength(shot: str, motion_word: str) -> float:
    """Blend the shot's default boldness with the director's low/medium/high hint."""
    base = _SHOT_MOTION.get(shot, 0.55)
    word = _MOTION_WORD.get((motion_word or "").strip().lower())
    if word is None:
        return round(base, 2)
    return round((base + word) / 2.0, 2)


def _beat_prompt(shot: str, ambient: str, raw_prompt: str) -> str:
    """Use the director's prompt; ensure the move + ambient motion are present."""
    prompt = (raw_prompt or "").strip()
    if not prompt:
        prompt = (
            f"Photorealistic live-action real-estate b-roll of this exact space; "
            f"{_SHOT_HINT.get(shot, _SHOT_HINT['slow push-in'])}. Natural light, "
            f"stable architecture, no people, no text."
        )
    if ambient and ambient.lower() not in prompt.lower():
        prompt = f"{prompt.rstrip('. ')}. {ambient[0].upper()}{ambient[1:]}."
    return prompt


def _fallback_plan(
    media_paths: list[str], instructions: str, address: str, walkthrough: bool = False
) -> TrailerPlan:
    """No-LLM plan: tour the photos with varied, deliberate cinematic moves.

    In walkthrough mode every beat is an embodied first-person walk-in.
    """
    shots = _WALKTHROUGH_SHOTS if walkthrough else [
        "dolly-in with parallax",
        "gentle pan right",
        "slow arc",
        "gentle pan left",
        "crane up reveal",
        "parallax drift",
    ]
    scenes: list[Scene] = []
    for i, path in enumerate(media_paths[: config.TRAILER_TARGET_BEATS]):
        shot = shots[i % len(shots)]
        scenes.append(
            Scene(
                index=i,
                source_path=path,
                prompt=_beat_prompt(shot, "", ""),
                caption="",
                time_label="",
                time_of_day="day",
                duration=config.SCENE_DURATION,
                shot=shot,
                motion_strength=_SHOT_MOTION.get(shot, 0.6),
                ambient="",
            )
        )
    title = (address or instructions or "Your next stay").strip()[:40]
    items = _assemble_items(title, scenes, {})
    return TrailerPlan(
        title=title, scenes=scenes, items=items, voiceover="", end_card={"name": title}
    )


# What the trailer may contain. Used to gate info cards + steer the director.
ALL_INCLUDES = {"photos", "map", "transit", "amenities", "host", "price"}


def _reorder_for_brief(media_paths: list[str], brief: dict | None) -> tuple[list[str], list[str]]:
    """Return (ordered_media_paths, asset_notes) honoring the brief's asset order.

    The marketing brief decides WHICH uploaded assets to use and in WHAT order;
    we reorder ``media_paths`` to match (clamping/ignoring out-of-range indices)
    so the Videographer films them in the directed sequence. Falls back to the
    original order when the brief has no assets.
    """
    if not brief:
        return media_paths, []
    assets = brief.get("assets") or []
    ordered: list[str] = []
    notes: list[str] = []
    seen: set[int] = set()
    for a in assets:
        try:
            idx = int(a.get("index"))
        except (TypeError, ValueError, AttributeError):
            continue
        if 0 <= idx < len(media_paths) and idx not in seen:
            ordered.append(media_paths[idx])
            notes.append(str(a.get("reason") or "").strip())
            seen.add(idx)
    if not ordered:
        return media_paths, []
    return ordered, notes


def build_trailer_plan(
    media_paths: list[str],
    instructions: str,
    address: str = "",
    lease: str = "",
    *,
    work_dir: Path | None = None,
    include: set[str] | None = None,
    brief: dict | None = None,
    grounding: str = "",
    walkthrough: bool = False,
) -> TrailerPlan:
    """Plan the vertical trailer with GPT-4o. Falls back on any error.

    When a marketing ``brief`` is supplied, its asset selection/order is honored
    and its hooks/captions/voiceover/pitch steer the director. ``grounding`` is
    the brand dossier's consistent fact sheet, prepended so prices/amenities and
    other (possibly fabricated-but-consistent) facts never drift between videos.

    ``walkthrough`` flips the director into embodied first-person mode: every beat
    becomes a POV walk-in through that room (Cosmos's world-model strength).
    """
    inc = include if include else set(ALL_INCLUDES)
    # The brief decides which uploaded assets to use, and in what order.
    media_paths, asset_notes = _reorder_for_brief(media_paths, brief)
    if not media_paths:
        return _fallback_plan(media_paths, instructions, address, walkthrough)
    if not config.OPENAI_API_KEY:
        print("[trailer] OPENAI_API_KEY not set -> heuristic plan")
        return _fallback_plan(media_paths, instructions, address, walkthrough)

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
        return _fallback_plan(media_paths, instructions, address, walkthrough)

    facts = instructions.strip() or "A charming property; highlight what makes a stay here special."
    context = ""
    if grounding.strip():
        context += f"Brand brief (KEEP ALL FACTS CONSISTENT WITH THIS):\n{grounding.strip()}\n\n"
    context += f"Listing facts / brief:\n{facts}"
    if address:
        context += f"\nProperty name / location: {address}"
    if lease:
        context += f"\nAvailability / price: {lease}"
    context += (
        f"\n\nThere are {len(encoded)} images, indices 0..{len(encoded) - 1}. "
        f"Plan about {config.TRAILER_TARGET_BEATS} beats."
    )

    # Marketing-brief steering: keep the directed asset ORDER and lean on its
    # creative direction (hooks/captions/voiceover/pitch).
    if brief:
        context += (
            "\n\nA marketing manager already selected and ORDERED these images for "
            "you (index 0 first). Keep this order; create one beat per image."
        )
        if any(asset_notes):
            note_lines = [
                f"- Image {i}: {n}" for i, n in enumerate(asset_notes) if n
            ]
            if note_lines:
                context += "\nWhy each was chosen:\n" + "\n".join(note_lines)
        hooks = [str(h).strip() for h in (brief.get("hooks") or []) if str(h).strip()]
        if hooks:
            context += "\nHooks to work in: " + " | ".join(hooks[:4])
        caps = [str(c).strip() for c in (brief.get("captions") or []) if str(c).strip()]
        if caps:
            context += "\nPreferred on-screen captions (reuse where they fit): " + " | ".join(caps[:8])
        if brief.get("voiceover"):
            context += f"\nUse this voiceover script (lightly polish only):\n{brief['voiceover']}"
        if brief.get("pitch"):
            context += f"\nOverall pitch/angle: {brief['pitch']}"

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
    if walkthrough:
        system_prompt += _WALKTHROUGH_DIRECTIVE

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
        return _fallback_plan(media_paths, instructions, address, walkthrough)

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
        if walkthrough and shot not in _WALKTHROUGH_SHOTS:
            # Keep the opening as an arrival; everything else is a walk-in.
            shot = _WALKTHROUGH_SHOTS[0] if i == 0 else _WALKTHROUGH_SHOTS[1 + (i % 3)]
        ambient = str(beat.get("ambient") or "").strip()[:120]
        motion_word = "high" if walkthrough else str(beat.get("motion", ""))
        motion_strength = _motion_strength(shot, motion_word)
        prompt = _beat_prompt(shot, ambient, str(beat.get("prompt") or ""))
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
                shot=shot,
                motion_strength=motion_strength,
                ambient=ambient,
            )
        )

    if not scenes:
        return _fallback_plan(media_paths, instructions, address, walkthrough)

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
