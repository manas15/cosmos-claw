"""North-star Goals: the ambitious targets the two agents chase for months.

Following the "loops and goals" idea, each project carries a few big, measurable
goals (e.g. 10k Instagram followers, 1M TikTok views, real community engagement).
The Planner conditions every campaign on the largest remaining GAP, and the loop
keeps running until the goals are met. Performance logged via ``feedback.py`` is
ingested here to advance ``current`` toward ``target``.

Goals live on the dossier under ``goals`` so they persist across restarts and
are use-case agnostic (followers/views/engagement apply to any brand). Override
the defaults per project from the CLI, API, or UI.
"""

from __future__ import annotations

from . import brand, listings

# Big, shared, multi-month targets — the experiment's whole point.
DEFAULT_GOALS: list[dict] = [
    {"id": "ig_followers", "platform": "instagram", "metric": "followers",
     "label": "Instagram followers", "target": 10000, "current": 0},
    {"id": "tt_views", "platform": "tiktok", "metric": "views",
     "label": "TikTok views", "target": 1000000, "current": 0},
    {"id": "community", "platform": "instagram", "metric": "comments",
     "label": "Community (comments + replies)", "target": 5000, "current": 0},
]

# Which social format feeds which platform's goals.
_FORMAT_PLATFORM = {"reel": "instagram", "tiktok": "tiktok"}


def load(listing_id: str) -> list[dict]:
    dossier = brand.load(listing_id) or {}
    return dossier.get("goals") or []


def save(listing_id: str, goals: list[dict]) -> None:
    dossier = brand.load(listing_id)
    if dossier is None:
        return
    dossier["goals"] = goals
    brand.save(listing_id, dossier)


def ensure(listing_id: str) -> list[dict]:
    """Seed the default north-star goals if this project has none yet."""
    goals = load(listing_id)
    if not goals:
        goals = [dict(g) for g in DEFAULT_GOALS]
        save(listing_id, goals)
    return goals


def set_target(listing_id: str, goal_id: str, target: float) -> list[dict]:
    goals = ensure(listing_id)
    for g in goals:
        if g["id"] == goal_id:
            g["target"] = float(target)
    save(listing_id, goals)
    return goals


def set_current(listing_id: str, goal_id: str, current: float) -> list[dict]:
    goals = ensure(listing_id)
    for g in goals:
        if g["id"] == goal_id:
            g["current"] = float(current)
    save(listing_id, goals)
    return goals


def ingest_performance(listing_id: str, fmt: str, metrics: dict) -> list[dict]:
    """Advance goal counters from a posted cut's metrics (followers/views/etc.)."""
    goals = ensure(listing_id)
    platform = _FORMAT_PLATFORM.get((fmt or "").lower())
    changed = False
    for g in goals:
        if platform and g.get("platform") and g["platform"] != platform:
            continue
        m = g.get("metric")
        if m in metrics:
            try:
                g["current"] = float(g.get("current", 0)) + float(metrics[m])
                changed = True
            except (TypeError, ValueError):
                pass
    if changed:
        save(listing_id, goals)
        for g in goals:
            if g["current"] >= g["target"]:
                brand.log_activity(
                    listing_id, "🏆", f"Goal reached: {g['label']} ({int(g['current'])}/{int(g['target'])})", "goal"
                )
    return goals


def progress(listing_id: str) -> list[dict]:
    """Goals annotated with completion percent (for UI/CLI)."""
    out = []
    for g in ensure(listing_id):
        target = float(g.get("target") or 0) or 1.0
        pct = max(0.0, min(100.0, 100.0 * float(g.get("current", 0)) / target))
        out.append({**g, "pct": round(pct, 1), "met": float(g.get("current", 0)) >= float(g.get("target") or 0)})
    return out


def all_met(listing_id: str) -> bool:
    goals = ensure(listing_id)
    return bool(goals) and all(float(g.get("current", 0)) >= float(g.get("target") or 0) for g in goals)


def gap_hint(listing_id: str) -> str:
    """A short instruction for the Planner: focus on the biggest remaining gap."""
    pending = [g for g in progress(listing_id) if not g["met"]]
    if not pending:
        return ""
    g = min(pending, key=lambda x: x["pct"])
    return (
        f"Top priority goal: {g['label']} at {int(g['current'])}/{int(g['target'])} "
        f"({g['pct']}%). Make content that drives {g['metric']} on "
        f"{g.get('platform') or 'social'} — strong hook, shareable, clear CTA."
    )
