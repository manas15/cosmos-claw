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
import os
import re
import time
from pathlib import Path

from . import config, listings


def _atomic_write(path: Path, text: str) -> None:
    """Write via a temp file + atomic rename so a crash/kill mid-write (the loop
    runs for months) can never leave a half-written, unparseable dossier."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def brand_path(listing_id: str) -> Path:
    return config.OUTPUT_DIR / f"listing_{listing_id}_brand.json"


def _now() -> float:
    return time.time()


def _default_dossier(listing: listings.Listing) -> dict:
    """A blank dossier scaffold (no agent run yet)."""
    return {
        "listing_id": listing.id,
        "name": listing.name,
        # Free-text use-case so the agent works for ANY project (rental, cafe,
        # gym, product, creator, event...). Drives tone, hashtags, and the CTA.
        "use_case": "",
        "brand": {
            "oneliner": "",
            "audience": "",
            "tone": "",
            "voice": config.TTS_VOICE,   # OpenAI TTS voice id
            "music": "warm",             # mood label
            "selling_points": [],
            "cta": "",                   # call to action (e.g. "Book now", "Visit us")
            "handle": "",                # social handle (@...) once chosen
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
        # What the agent learned from human feedback + performance (Phase 3):
        # short "avoid X" / "do more Y" notes fed back into ideation.
        "lessons": [],
        # Milestone summaries the weekly reflection folds older history into.
        "chronicle": [],
        "updated_at": _now(),
    }


def add_lessons(listing_id: str, new_lessons: list[str], cap: int = 40) -> dict | None:
    """Append de-duplicated lessons learned (kept bounded). Returns the dossier."""
    dossier = load(listing_id)
    if dossier is None:
        return None
    existing = dossier.setdefault("lessons", [])
    seen = {str(x).strip().lower() for x in existing}
    for lesson in new_lessons:
        s = str(lesson).strip()
        if s and s.lower() not in seen:
            existing.append(s)
            seen.add(s.lower())
    if len(existing) > cap:  # keep the most recent lessons
        dossier["lessons"] = existing[-cap:]
    save(listing_id, dossier)
    return dossier


def _fold_chronicle(dossier: dict, summary: str) -> None:
    """Append a bounded one-line milestone to the chronicle."""
    chron = dossier.setdefault("chronicle", [])
    chron.append({"ts": _now(), "summary": summary})
    if len(chron) > config.CHRONICLE_CAP:
        dossier["chronicle"] = chron[-config.CHRONICLE_CAP:]


def compact(listing_id: str) -> dict | None:
    """Keep the dossier bounded over a months-long run.

    When the append-only ``activity``/``research`` logs grow past their caps the
    oldest entries are dropped, but a single chronicle line records how many were
    folded away (and the date range) so the agent keeps a coarse long-term memory.
    """
    dossier = load(listing_id)
    if dossier is None:
        return None
    changed = False

    activity = dossier.get("activity", [])
    if len(activity) > config.ACTIVITY_CAP:
        drop = activity[: len(activity) - config.ACTIVITY_CAP]
        kept = activity[len(activity) - config.ACTIVITY_CAP:]
        first = time.strftime("%Y-%m-%d", time.localtime(drop[0].get("ts", _now())))
        last = time.strftime("%Y-%m-%d", time.localtime(drop[-1].get("ts", _now())))
        _fold_chronicle(dossier, f"Folded {len(drop)} earlier activity entries ({first} → {last}).")
        dossier["activity"] = kept
        changed = True

    research = dossier.get("research", [])
    if len(research) > config.RESEARCH_CAP:
        dossier["research"] = research[-config.RESEARCH_CAP:]
        changed = True

    if changed:
        save(listing_id, dossier)
    return dossier


def reflect(listing_id: str, *, force: bool = False) -> bool:
    """Weekly reflection: distil durable lessons and write a chronicle milestone.

    Runs at most once per ``config.REFLECT_EVERY`` window (tracked in the dossier)
    unless ``force`` is set. Returns True when a reflection actually happened.
    """
    dossier = load(listing_id)
    if dossier is None:
        return False
    last = float(dossier.get("last_reflect_at") or 0)
    if not force and (_now() - last) < config.REFLECT_EVERY:
        return False

    # Distil lessons from feedback (lazy import avoids a circular dependency).
    try:
        from . import feedback

        feedback.derive_lessons(listing_id)
    except Exception:  # noqa: BLE001
        pass

    posted = len(listings.list_versions(listing_id))
    progress_bits: list[str] = []
    try:
        from . import goals

        for g in goals.progress(listing_id):
            progress_bits.append(f"{g['label']} {round(g['pct'], 1)}%")
    except Exception:  # noqa: BLE001
        pass

    dossier = load(listing_id) or dossier  # reload after derive_lessons saved
    summary = f"Weekly reflection — {posted} cuts on disk"
    if progress_bits:
        summary += "; " + ", ".join(progress_bits)
    _fold_chronicle(dossier, summary)
    dossier["last_reflect_at"] = _now()
    save(listing_id, dossier)
    return True


def seed_from_listing(listing: listings.Listing) -> dict:
    """Build (and persist) a starting dossier from a project's optional details.

    A project is just a folder of photos. If extra context exists (an Airbnb-style
    PDF, a notes file, structured details) it's folded in as free-form facts, but
    NOTHING here is required — a bare photo folder for any use-case seeds fine.
    """
    dossier = _default_dossier(listing)
    try:
        det = listings.extract_details(listing)
    except Exception:  # noqa: BLE001
        det = {}
    # Store whatever we found as free-form facts (no rental-specific schema).
    facts = {k: v for k, v in (det or {}).items() if v not in (None, "", [], False, {})}
    dossier["facts"] = facts
    dossier["use_case"] = str(det.get("kind") or det.get("use_case") or "").strip()
    highlights = det.get("amenities") or det.get("highlights") or det.get("features") or []
    dossier["brand"]["selling_points"] = list(highlights)[:5]
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
    _atomic_write(brand_path(listing_id), json.dumps(dossier, indent=2))


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
    name = facts.get("title") or dossier.get("name") or "brand"
    slug = re.sub(r"[^a-z0-9]", "", name.lower())[:24] or "brand"
    return f"@{slug}"


def _hashtags(dossier: dict, fmt_label: str) -> list[str]:
    """Build a small, relevant hashtag set from the brand itself (use-case
    agnostic): location, use-case words, brand name, and the platform format."""
    facts = dossier.get("facts") or {}
    brand_ = dossier.get("brand") or {}
    tags: list[str] = []
    seen: set[str] = set()

    def add(text: str) -> None:
        token = "".join(w.capitalize() for w in re.findall(r"[A-Za-z0-9]+", text or ""))
        key = token.lower()
        if token and key not in seen:
            seen.add(key)
            tags.append(f"#{token}")

    for part in re.split(r"[,/]", facts.get("location") or ""):
        add(part)
    for word in re.split(r"[\s,/&-]+", dossier.get("use_case") or ""):
        if len(word) > 2:
            add(word)
    add(dossier.get("name") or "")

    fl = (fmt_label or "").lower()
    if "youtube" in fl or "short" in fl:
        add("Shorts")
    elif any(k in fl for k in ("reel", "tiktok", "story", "snap")):
        add("Reels")
    # A couple of evergreen growth tags that fit any vertical.
    for t in ("trending", "fyp"):
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
    cta = (brand_.get("cta") or "").strip() or "👉 Link in bio"
    blocks.append(cta)

    hashtags = _hashtags(dossier, fmt_label)
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

    if dossier.get("use_case"):
        lines.append(f"Use case: {dossier['use_case']}")
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
        ("Name", "title"), ("Location", "location"), ("Price", "price"),
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
