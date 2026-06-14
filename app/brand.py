"""Brand dossier: the marketing-manager agent's persistent memory per project.

Each project (listing) gets one ``listing_{id}_brand.json`` in ``OUTPUT_DIR`` that
holds the brand positioning, the *consistent* fabricated assumptions, the merged
facts, the research log, the creative brief for the Videographer, and an activity
timeline. It is the single source of truth: the terminal CLI, the GPT agent, and
the UI all read/write it, and every generation grounds on it so made-up facts
(price, amenities, neighborhood, host story) stay identical across videos.

Mirrors the simple JSON-in-OUTPUT_DIR pattern used in ``listings.py``.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import config, listings

# Fact keys we carry from the structured listing details into the dossier.
_FACT_KEYS = (
    "title", "location", "price", "guests", "bedrooms", "beds", "baths",
    "rating", "reviews", "host", "summary", "superhost", "amenities", "nearby",
)


def brand_path(listing_id: str) -> Path:
    return config.OUTPUT_DIR / f"listing_{listing_id}_brand.json"


def _now() -> float:
    return time.time()


def _default_dossier(listing: listings.Listing) -> dict:
    """A blank dossier scaffold (no agent run yet)."""
    return {
        "listing_id": listing.id,
        "name": listing.name,
        "brand": {
            "oneliner": "",
            "audience": "",
            "tone": "",
            "voice": config.TTS_VOICE,   # OpenAI TTS voice id
            "music": "warm",             # mood label
            "selling_points": [],
        },
        # Each: {"field", "value", "source": "assumed"} - the consistency contract.
        "assumptions": [],
        # Merged truth = real listing facts (+ assumptions layered on top).
        "facts": {},
        # Each: {"query", "source", "snippet", "kind": "real"|"assumed"}
        "research": [],
        # Creative brief the Videographer consumes.
        "brief": {
            "format": config.DEFAULT_FORMAT,
            "assets": [],          # [{"index": int, "reason": str}]
            "rationale": "",
            "hooks": [],
            "captions": [],
            "voiceover": "",
            "music": "warm",
            "voice": config.TTS_VOICE,
            "pitch": "",
        },
        # Timeline entries surfaced in the Agent Loop feed.
        "activity": [],
        "updated_at": _now(),
    }


def seed_from_listing(listing: listings.Listing) -> dict:
    """Build (and persist) a starting dossier from the listing's parsed facts."""
    dossier = _default_dossier(listing)
    try:
        det = listings.extract_details(listing)
    except Exception:  # noqa: BLE001
        det = {}
    facts: dict = {}
    for k in _FACT_KEYS:
        default = [] if k in ("amenities", "nearby") else (False if k == "superhost" else "")
        facts[k] = det.get(k, default)
    dossier["facts"] = facts
    dossier["brand"]["selling_points"] = list(det.get("amenities") or [])[:5]
    save(listing.id, dossier)
    return dossier


def load(listing_id: str) -> dict | None:
    """Read the dossier from disk, or None if it has never been created."""
    p = brand_path(listing_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def load_or_seed(listing: listings.Listing) -> dict:
    """Load the dossier for this listing, seeding one from facts if absent."""
    existing = load(listing.id)
    return existing if existing is not None else seed_from_listing(listing)


def save(listing_id: str, dossier: dict) -> None:
    dossier["updated_at"] = _now()
    brand_path(listing_id).write_text(json.dumps(dossier, indent=2), encoding="utf-8")


def log_activity(listing_id: str, icon: str, text: str, kind: str = "action") -> dict | None:
    """Append a timeline entry so the UI feed reflects manual + agent steps."""
    dossier = load(listing_id)
    if dossier is None:
        return None
    dossier.setdefault("activity", []).append(
        {"ts": _now(), "icon": icon, "text": text, "kind": kind}
    )
    save(listing_id, dossier)
    return dossier


def add_assumption(listing_id: str, field: str, value: str) -> dict | None:
    """Record a fabricated fact ONCE and layer it onto facts (never overwritten).

    Keeping assumptions append-only is what guarantees consistency across videos.
    """
    dossier = load(listing_id)
    if dossier is None:
        return None
    existing = {a.get("field") for a in dossier.get("assumptions", [])}
    if field not in existing:
        dossier.setdefault("assumptions", []).append(
            {"field": field, "value": value, "source": "assumed"}
        )
        dossier.setdefault("facts", {})[field] = value
        dossier.setdefault("activity", []).append(
            {"ts": _now(), "icon": "🧩", "text": f"Assumed {field}: {value}", "kind": "assume"}
        )
        save(listing_id, dossier)
    return dossier


def set_path(dossier: dict, dotpath: str, value) -> dict:
    """Set a nested key by dotted path (e.g. 'brand.voice'); used by the CLI."""
    keys = dotpath.split(".")
    node = dossier
    for k in keys[:-1]:
        node = node.setdefault(k, {})
    node[keys[-1]] = value
    return dossier


def post_handle(dossier: dict) -> str:
    """The (assumed) social handle for this brand — we own the account.

    Prefers an explicit brand handle, then the property's real title (more
    on-brand than the folder name), then the project name.
    """
    existing = (dossier.get("brand") or {}).get("handle")
    if existing:
        return existing
    facts = dossier.get("facts") or {}
    name = facts.get("title") or dossier.get("name") or "stay"
    slug = re.sub(r"[^a-z0-9]", "", name.lower())[:24] or "stay"
    return f"@{slug}"


def _hashtags(facts: dict, fmt_label: str) -> list[str]:
    """Build a small, relevant hashtag set from location + the format."""
    tags: list[str] = []
    seen: set[str] = set()

    def add(tag: str) -> None:
        key = tag.lower()
        if tag and key not in seen:
            seen.add(key)
            tags.append(tag)

    location = facts.get("location") or ""
    for part in re.split(r"[,/]", location):
        token = "".join(w.capitalize() for w in re.findall(r"[A-Za-z0-9]+", part))
        if token:
            add(f"#{token}")

    fl = (fmt_label or "").lower()
    if "youtube" in fl or "short" in fl:
        add("#Shorts")
    elif any(k in fl for k in ("reel", "tiktok", "story", "snap")):
        add("#Reels")

    for t in ("#Airbnb", "#VacationRental", "#TravelGoals", "#StayHere"):
        add(t)
    return tags[:8]


def build_post(dossier: dict, fmt_label: str = "") -> dict:
    """Assemble a copy-paste-ready social post (handle, caption, hashtags, audio).

    Assumes we own the brand handle and are ready to publish: composes the caption
    from the brief's hook/pitch + selling points + a CTA, then appends hashtags.
    Deterministic (no extra API call) so it stays consistent with the dossier.
    """
    brand_ = dossier.get("brand", {})
    brief = dossier.get("brief", {})
    facts = dossier.get("facts", {})

    hooks = [h for h in (brief.get("hooks") or []) if str(h).strip()]
    pitch = brief.get("pitch") or brand_.get("oneliner") or facts.get("summary") or ""
    selling = brand_.get("selling_points") or facts.get("amenities") or []

    blocks: list[str] = []
    if hooks:
        blocks.append(str(hooks[0]).strip())
    if pitch and str(pitch).strip() not in blocks:
        blocks.append(str(pitch).strip())
    bullets = [f"✨ {str(s).strip()}" for s in selling[:3] if str(s).strip()]
    if bullets:
        blocks.append("\n".join(bullets))
    info = []
    if facts.get("location"):
        info.append(f"📍 {facts['location']}")
    if facts.get("price"):
        info.append(f"💰 {facts['price']}")
    if info:
        blocks.append("  ·  ".join(info))
    blocks.append("📩 DM to book — link in bio")

    hashtags = _hashtags(facts, fmt_label)
    caption = "\n\n".join(blocks)
    if hashtags:
        caption = f"{caption}\n\n{' '.join(hashtags)}"

    return {
        "handle": post_handle(dossier),
        "caption": caption,
        "hashtags": hashtags,
        "music": brief.get("music") or brand_.get("music") or "warm",
    }


def grounding_text(dossier: dict) -> str:
    """Build a grounding brief string for the Videographer from the dossier.

    Includes the brand positioning, the merged (real + assumed) facts, and the
    pitch, so every generation reuses the SAME facts. Assumed values are folded
    in as if true (the demo explicitly allows made-up but consistent facts).
    """
    if not dossier:
        return ""
    brand = dossier.get("brand", {})
    facts = dossier.get("facts", {})
    brief = dossier.get("brief", {})
    lines: list[str] = []

    if brand.get("oneliner"):
        lines.append(f"Positioning: {brand['oneliner']}")
    if brand.get("audience"):
        lines.append(f"Target audience: {brand['audience']}")
    if brand.get("tone"):
        lines.append(f"Tone: {brand['tone']}")
    sp = brand.get("selling_points") or []
    if sp:
        lines.append("Selling points: " + ", ".join(sp))

    fact_lines: list[str] = []
    for label, key in (
        ("Property", "title"), ("Location", "location"), ("Price", "price"),
        ("Host", "host"), ("Rating", "rating"),
    ):
        v = facts.get(key)
        if v:
            fact_lines.append(f"{label}: {v}")
    layout = " · ".join(
        str(facts.get(k)) for k in ("guests", "bedrooms", "beds", "baths") if facts.get(k)
    )
    if layout:
        fact_lines.append(f"Layout: {layout}")
    amenities = facts.get("amenities") or []
    if amenities:
        fact_lines.append("Amenities: " + ", ".join(amenities))
    nearby = facts.get("nearby") or []
    if nearby:
        fact_lines.append("Nearby: " + ", ".join(nearby))
    if fact_lines:
        lines.append("Facts:\n" + "\n".join(fact_lines))

    if brief.get("pitch"):
        lines.append(f"Pitch: {brief['pitch']}")

    return "\n".join(lines).strip()
