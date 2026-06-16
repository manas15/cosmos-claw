"""The Checker: human-in-the-loop feedback that teaches the agents.

Every published reel starts ``pending_review``. A human (via the UI or the CLI)
either POSTS it (it goes live; a 7-day performance check-in is scheduled) or
DISCARDS it as slop (with a note on *why*). Later they log how a posted reel
PERFORMED (views/likes/followers). All of this is stored on the version's meta
JSON and distilled into short "lessons" on the brand dossier that steer future
ideation — so the AI slop drops over the first ~10 generations.

State lives on each version's meta (status, posted_at, review_due, slop_notes,
performance) and on the dossier (``lessons``). Performance metrics are also
routed to the matching platform goal (see ``goals.py``).
"""

from __future__ import annotations

import time

from . import brand, config, listings

REVIEW_WINDOW_S = 7 * 24 * 3600  # ask "how did it do?" a week after posting

VALID_DECISIONS = ("posted", "discarded")


def _meta(listing_id: str, vid: str) -> dict:
    _, _, mpath = listings.version_paths(listing_id, vid)
    return listings._read_json(mpath)


def _save(listing_id: str, vid: str, meta: dict) -> None:
    listings.save_version_meta(listing_id, vid, meta)


def record_decision(listing_id: str, vid: str, decision: str, slop_notes: str = "") -> dict:
    """POST or DISCARD a pending reel. Returns the updated version meta."""
    decision = decision.lower().strip()
    if decision not in VALID_DECISIONS:
        raise ValueError(f"decision must be one of {VALID_DECISIONS}")
    meta = _meta(listing_id, vid)
    if not meta:
        raise ValueError(f"unknown version {listing_id}/{vid}")
    now = time.time()
    meta["status"] = decision
    meta["slop_notes"] = (slop_notes or "").strip()
    if decision == "posted":
        meta["posted_at"] = now
        meta["review_due"] = now + REVIEW_WINDOW_S
        brand.log_activity(listing_id, "📮", f"Posted “{meta.get('title') or vid}” — live now", "feedback")
    else:
        meta["posted_at"] = None
        meta["review_due"] = None
        note = f" — {slop_notes}" if slop_notes else ""
        brand.log_activity(listing_id, "🗑️", f"Discarded “{meta.get('title') or vid}” as slop{note}", "feedback")
    _save(listing_id, vid, meta)
    # A discard with a reason is the strongest learning signal — capture it now.
    if decision == "discarded" and slop_notes:
        brand.add_lessons(listing_id, [f"Avoid: {slop_notes.strip()}"])
    return meta


def record_performance(listing_id: str, vid: str, metrics: dict) -> dict:
    """Log how a posted reel performed (views/likes/followers/etc.).

    Clears the review reminder, stores the metrics, nudges goals, and (if the
    numbers are strong) records a "what performed" lesson.
    """
    meta = _meta(listing_id, vid)
    if not meta:
        raise ValueError(f"unknown version {listing_id}/{vid}")
    clean = {}
    for k, v in (metrics or {}).items():
        try:
            clean[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    meta["performance"] = {"ts": time.time(), "metrics": clean}
    meta["review_due"] = None
    if meta.get("status") != "posted":
        meta["status"] = "posted"
        meta.setdefault("posted_at", time.time())
    _save(listing_id, vid, meta)

    views = clean.get("views") or clean.get("plays") or 0
    brand.log_activity(
        listing_id, "📈",
        f"Logged performance for “{meta.get('title') or vid}”: "
        + ", ".join(f"{k}={int(v)}" for k, v in clean.items()),
        "feedback",
    )
    # Route metrics to the matching platform goals (best-effort import to avoid
    # a hard dependency cycle before goals exist).
    try:
        from . import goals

        goals.ingest_performance(listing_id, meta.get("format") or "", clean)
    except Exception:  # noqa: BLE001
        pass
    if views >= 1000:  # a hit — remember what worked
        brand.add_lessons(
            listing_id,
            [f"Do more like “{meta.get('title') or vid}” ({meta.get('angle') or meta.get('format')}) — {int(views)} views"],
        )
    return meta


def _versions(listing_id: str) -> list[dict]:
    return listings.list_versions(listing_id)


def pending_reviews(listing_id: str) -> list[dict]:
    """Published cuts still awaiting a human post/discard decision."""
    out = []
    for v in _versions(listing_id):
        if (v["meta"].get("status") or "pending_review") == "pending_review":
            out.append(v)
    return out


def due_reviews(listing_id: str, now: float | None = None) -> list[dict]:
    """Posted cuts whose 7-day performance check-in is due (no metrics yet)."""
    now = now or time.time()
    out = []
    for v in _versions(listing_id):
        m = v["meta"]
        due = m.get("review_due")
        if m.get("status") == "posted" and not m.get("performance") and due and due <= now:
            out.append(v)
    return out


def what_performed(listing_id: str, top: int = 5) -> list[str]:
    """Short summaries of the best-performing posted cuts (for ideation)."""
    scored = []
    for v in _versions(listing_id):
        perf = (v["meta"].get("performance") or {}).get("metrics") or {}
        views = perf.get("views") or perf.get("plays") or 0
        if views:
            scored.append((views, v["meta"]))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [
        f"{m.get('theme') or m.get('title') or m.get('format')} → {int(views)} views"
        for views, m in scored[:top]
    ]


def slop_to_avoid(listing_id: str, top: int = 8) -> list[str]:
    """Reasons humans discarded cuts (so ideation can steer clear)."""
    notes = []
    for v in _versions(listing_id):
        m = v["meta"]
        if m.get("status") == "discarded" and m.get("slop_notes"):
            notes.append(m["slop_notes"].strip())
    return notes[-top:]


def derive_lessons(listing_id: str, use_gpt: bool = True) -> list[str]:
    """Distil discard reasons + top performers into concise, durable lessons.

    Deterministic by default; if an OpenAI key is present, GPT compresses the raw
    signals into a tight, de-duplicated list. Persists onto the dossier.
    """
    avoid = slop_to_avoid(listing_id)
    performed = what_performed(listing_id)
    lessons: list[str] = [f"Avoid: {n}" for n in avoid] + [f"Repeat: {p}" for p in performed]

    if use_gpt and config.OPENAI_API_KEY and (avoid or performed):
        try:
            import json as _json

            from . import marketing_agent

            data = marketing_agent._gpt_json(
                "You are a social-media strategist. Turn these raw feedback signals "
                "into at most 8 short, concrete, non-duplicative lessons (each < 16 "
                'words) for the next videos. Return STRICT JSON {"lessons": ["..."]}.',
                _json.dumps({"discard_reasons": avoid, "top_performers": performed}),
                max_tokens=500, temperature=0.3,
            )
            gpt_lessons = [str(x).strip() for x in (data.get("lessons") or []) if str(x).strip()]
            if gpt_lessons:
                lessons = gpt_lessons
        except Exception:  # noqa: BLE001
            pass

    if lessons:
        brand.add_lessons(listing_id, lessons)
    return (brand.load(listing_id) or {}).get("lessons", [])
