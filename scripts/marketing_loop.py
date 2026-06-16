"""Always-on marketing-manager loop for Cosmos Claw.

Thinks like a social-media manager for each project and keeps the Agent Loop
busy with fresh, ready-to-post cuts:

  1. STUDY  — GPT-4o (vision) labels every uploaded photo once (cached in the
              brand dossier as the asset index).
  2. IDEATE — GPT-4o brainstorms ONE fresh campaign distinct from past themes:
              an angle, which photos (in order), the social format, a music
              mood, a TTS voice, a ready-to-post caption + hashtags, and a
              ~25s spoken VOICEOVER script.
  3-5. FILM/CUT/PUBLISH — delegated to the Videographer skill
              (``app.videographer.make_reel``): short embodied clips on the
              active backend, cross-faded into the format, voiced + scored,
              published as a listing version so it lands in the Agent Loop feed.

It loops, alternating projects, until --max-videos cuts are published (or, in
later phases, until the project goals are met). Each campaign is independent:
one failure is logged and the loop moves on.

Run (live Cosmos endpoint + tunnel up):
  .venv/bin/python scripts/marketing_loop.py --max-videos 6
Dry-run without a GPU:
  .venv/bin/python scripts/marketing_loop.py --backend stub --max-videos 2
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import brand, config, feedback, goals, listings, marketing_agent, videographer
from app.generation.factory import get_generator
from app.vision import analyze as _analyze

_VOICES = ("alloy", "echo", "fable", "onyx", "nova", "shimmer")
_MOODS = ("warm", "calm", "uplifting", "energetic", "luxury", "moody")
_FORMATS = tuple(config.FORMAT_PRESETS.keys())

_DEFAULT_PROJECTS = ["la-house-1", "hacker-house"]


# --- the manager's brain ------------------------------------------------

_IDEA_SYSTEM = (
    "You are the always-on social-media MARKETING MANAGER for a single brand. "
    "You own its social handle and ship short-form video constantly. Brainstorm "
    "ONE fresh campaign for the next post that is clearly DIFFERENT from the past "
    "themes you are given (different angle, hook, scene mix, format, and energy). "
    "Ground every claim ONLY on the brand facts provided — never contradict them. "
    "Pick photos by their index from the asset list so the cut tells a little "
    "story. Write a punchy, ready-to-post caption (no hashtags inside it) and a "
    "spoken VOICEOVER script of about 55-70 words (~25 seconds) that a narrator "
    "reads over the video: a hook, 2-3 quick selling beats, and a call to action. "
    "Return STRICT JSON only with keys: theme, angle, format, music, voice, "
    "photo_indices, caption, voiceover, hashtags."
)


def _campaign_idea(dossier: dict, asset_index: list[dict], past_themes: list[str],
                   target_seconds: float, *, goal_hint: str = "", lessons: list[str] | None = None,
                   slop: list[str] | None = None, performed: list[str] | None = None) -> dict:
    """Ask GPT-4o for one fresh campaign grounded on the dossier + assets.

    ``goal_hint`` (the north-star gap), ``lessons`` (durable learnings),
    ``slop`` (recent discard reasons to avoid) and ``performed`` (what got views)
    steer the idea so the loop both advances the goal and reduces slop over time.
    """
    facts = dossier.get("facts") or {}
    bnd = dossier.get("brand") or {}
    assets = [{"index": a["index"], "space": a.get("label", "")} for a in asset_index]
    user = {
        "brand": {
            "name": dossier.get("name"),
            "oneliner": bnd.get("oneliner", ""),
            "tone": bnd.get("tone", ""),
            "audience": bnd.get("audience", ""),
            "use_case": dossier.get("use_case", ""),
            "selling_points": bnd.get("selling_points", []),
        },
        "facts": {k: facts.get(k) for k in ("title", "location", "price", "summary",
                                            "amenities", "nearby")},
        "available_photos": assets,
        "past_themes": past_themes[-12:],
        "allowed_formats": list(_FORMATS),
        "allowed_music_moods": list(_MOODS),
        "allowed_voices": list(_VOICES),
        "target_seconds": target_seconds,
        "want_photos": "pick 9 to 12 indices, ordered to tell a story (short clips, snappy cuts)",
    }
    if goal_hint:
        user["goal"] = goal_hint
    if lessons:
        user["lessons_learned"] = lessons[-12:]
    if slop:
        user["avoid_these_mistakes"] = slop[-8:]
    if performed:
        user["what_performed_well"] = performed[-5:]
    import json as _json
    data = marketing_agent._gpt_json(_IDEA_SYSTEM, _json.dumps(user), max_tokens=900, temperature=0.85)
    return _sanitize_idea(data, asset_index, dossier)


def _sanitize_idea(data: dict, asset_index: list[dict], dossier: dict) -> dict:
    """Clamp the model's idea to valid choices, with sensible fallbacks."""
    n = len(asset_index)
    valid = {a["index"] for a in asset_index}

    raw_idx = data.get("photo_indices") or []
    if isinstance(raw_idx, str):
        raw_idx = re.findall(r"\d+", raw_idx)
    idx = []
    for v in raw_idx:
        try:
            iv = int(v)
        except (TypeError, ValueError):
            continue
        if iv in valid and iv not in idx:
            idx.append(iv)
    if len(idx) < 6:  # fall back to an even spread across the library
        step = max(1, n // 10)
        idx = list(range(0, n, step))[:10] or list(range(min(n, 10)))
    idx = idx[:12]

    fmt = str(data.get("format") or "").strip().lower()
    if fmt not in config.FORMAT_PRESETS:
        fmt = "reel"
    music = str(data.get("music") or "").strip().lower()
    if music not in _MOODS:
        music = (dossier.get("brand") or {}).get("music") or "uplifting"
    voice = str(data.get("voice") or "").strip().lower()
    if voice not in _VOICES:
        voice = (dossier.get("brand") or {}).get("voice") or config.TTS_VOICE

    theme = str(data.get("theme") or "").strip() or "Fresh look at the space"
    caption = str(data.get("caption") or "").strip() or theme
    voiceover = str(data.get("voiceover") or "").strip()
    raw_tags = data.get("hashtags") or []
    if isinstance(raw_tags, str):
        raw_tags = re.split(r"[\s,]+", raw_tags)
    hashtags = [str(h).strip() for h in raw_tags if str(h).strip()]
    hashtags = [h if h.startswith("#") else f"#{h}" for h in hashtags][:8]

    return {
        "theme": theme[:70],
        "angle": str(data.get("angle") or "").strip()[:160],
        "format": fmt,
        "music": music,
        "voice": voice,
        "photo_indices": idx,
        "caption": caption,
        "voiceover": voiceover,
        "hashtags": hashtags,
    }


# --- the manager's hands (study) ----------------------------------------


def _photos_for(lst: listings.Listing) -> list[str]:
    """A stable JPEG path per photo index (thumb cache normalizes avif/heic)."""
    out = []
    for i in range(len(lst.photos)):
        t = listings.thumb(lst, i)
        out.append(str(t) if t else str(lst.photos[i]))
    return out


def _asset_index(lst: listings.Listing, dossier: dict, work: Path, *,
                 use_vision: bool, reindex: bool) -> list[dict]:
    """Label every photo once (vision) and cache it in the dossier."""
    cached = dossier.get("asset_index")
    if cached and not reindex and len(cached) == len(lst.photos):
        return cached
    brand.log_activity(lst.id, "🧠", f"Studying the brand — reviewing {len(lst.photos)} assets", "research")
    photos = _photos_for(lst)
    context = (dossier.get("brand") or {}).get("oneliner") or dossier.get("use_case") or ""
    index: list[dict] = []
    for i, p in enumerate(photos):
        info = _analyze(p, work, i, use_vision=use_vision, context=context)
        index.append({"index": i, "path": p, "label": info["label"],
                      "shot": info["shot"], "prompt": info["prompt"]})
        print(f"   · asset {i + 1}/{len(photos)}: {info['label']!r}")
    dossier = brand.load(lst.id) or dossier
    dossier["asset_index"] = index
    brand.save(lst.id, dossier)
    brand.log_activity(lst.id, "🗂️", f"Built an asset index of {len(index)} spaces", "research")
    return index


# --- resilience ---------------------------------------------------------


def _ensure_ready(gen, attempts: int = 60, wait: float = 20.0) -> bool:
    """Block until the backend is genuinely reachable (active ``gen.live()``
    probe, model-agnostic). A wifi/tunnel/provider blip PAUSES the loop instead
    of burning a campaign (~60*20s = up to 20 min)."""
    for i in range(attempts):
        if gen.live():
            return True
        if i == 0 or (i + 1) % 3 == 0:
            print(f"  … backend down — pausing for it to recover ({i + 1}/{attempts})")
        time.sleep(wait)
    return False


def main() -> None:
    ap = argparse.ArgumentParser(description="Always-on marketing-manager video loop")
    ap.add_argument("--projects", default=",".join(_DEFAULT_PROJECTS),
                    help="comma-separated listing ids to manage")
    ap.add_argument("--max-videos", type=int, default=6, help="total cuts to publish")
    ap.add_argument("--until-goals", action="store_true",
                    help="keep going (up to --max-videos) until every project hits its north-star goals")
    ap.add_argument("--backend", default="", help="cosmos | stub | <dotted path> (default from .env)")
    ap.add_argument("--target-seconds", type=float, default=24.0)
    ap.add_argument("--max-frames", type=int, default=49,
                    help="cap frames/clip so MP4s stay small + transfer reliably (~49 ≈ 2s)")
    ap.add_argument("--best-of-n", type=int, default=int(config.COSMOS_BEST_OF_N),
                    help="render N takes per beat and auto-pick the steadiest (>1 doubles GPU)")
    ap.add_argument("--xdur", type=float, default=0.35, help="transition seconds")
    ap.add_argument("--sleep", type=float, default=0.0, help="pause between campaigns (s)")
    ap.add_argument("--no-vision", action="store_true", help="skip GPT vision asset labels")
    ap.add_argument("--reindex", action="store_true", help="rebuild the asset index")
    ap.add_argument("--tag", default="",
                    help="namespace the scratch dir so parallel workers don't clobber clips")
    args = ap.parse_args()

    project_ids = [p.strip() for p in args.projects.split(",") if p.strip()]
    projects: list[listings.Listing] = []
    for pid in project_ids:
        lst = listings.get_listing(pid)
        if lst is None:
            print(f"! unknown project '{pid}', skipping")
            continue
        projects.append(lst)
    if not projects:
        raise SystemExit("no valid projects (try --projects la-house-1,hacker-house)")

    gen = get_generator(args.backend or None)
    ok, why = gen.available()
    print(f"backend: {gen.name} | ready: {ok} | {why}")
    if not ok and not _ensure_ready(gen):
        raise SystemExit("generation backend not ready (Cosmos tunnel up? .env=cosmos?)")

    work = config.UPLOAD_DIR / (f"_mkt_{args.tag}" if args.tag else "_mkt")
    work.mkdir(parents=True, exist_ok=True)

    # STUDY every project once.
    dossiers: dict[str, dict] = {}
    indexes: dict[str, list[dict]] = {}
    for lst in projects:
        d = brand.load_or_seed(lst)
        dossiers[lst.id] = d
        goals.ensure(lst.id)  # seed the north-star goals if absent
        for g in goals.progress(lst.id):
            print(f"  🎯 {lst.id}: {g['label']} {int(g['current'])}/{int(g['target'])} ({g['pct']}%)")
        indexes[lst.id] = _asset_index(lst, d, work, use_vision=not args.no_vision,
                                        reindex=args.reindex)

    # IDEATE → FILM → PUBLISH, alternating projects.
    published = 0
    round_i = 0
    while published < args.max_videos:
        # Stop early once every project has hit its north-star goals (the
        # experiment succeeded). Without --until-goals we just keep producing.
        if args.until_goals and all(goals.all_met(p.id) for p in projects):
            print("\n🏆 all projects hit their north-star goals — experiment complete")
            break

        lst = projects[round_i % len(projects)]
        round_i += 1
        lid = lst.id

        if args.until_goals and goals.all_met(lid):
            print(f"  ✓ {lid} goals met — skipping")
            continue

        print(f"\n=== {lst.name} ({lid}) · cut {published + 1}/{args.max_videos} ===")

        if not _ensure_ready(gen):
            print("  ! backend unavailable; stopping loop")
            break

        # CHECKER: learn from any human feedback before ideating the next cut.
        try:
            feedback.derive_lessons(lid)
            for v in feedback.due_reviews(lid):
                brand.log_activity(
                    lid, "⏰",
                    f"Performance check-in due for “{v['meta'].get('title') or v['vid']}” — how did it do?",
                    "feedback",
                )
            n_pending = len(feedback.pending_reviews(lid))
            if n_pending:
                print(f"  · {n_pending} cut(s) awaiting your post/discard decision")
        except Exception as exc:  # noqa: BLE001
            print(f"  ! feedback pass failed ({exc})")

        dossier = brand.load(lid) or dossiers[lid]
        past = [c.get("theme", "") for c in dossier.get("campaigns", [])]
        lessons = dossier.get("lessons", [])
        slop = feedback.slop_to_avoid(lid)
        performed = feedback.what_performed(lid)
        goal_hint = goals.gap_hint(lid)
        if goal_hint:
            brand.log_activity(lid, "🎯", goal_hint, "goal")
        try:
            idea = _campaign_idea(dossier, indexes[lid], past, args.target_seconds,
                                  goal_hint=goal_hint, lessons=lessons, slop=slop, performed=performed)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! ideation failed ({exc}); using a generic idea")
            idea = _sanitize_idea({}, indexes[lid], dossier)

        brand.log_activity(lid, "💡", f"New campaign idea — “{idea['theme']}”", "idea")
        if idea.get("angle"):
            brand.log_activity(lid, "📝", idea["angle"], "idea")
        try:
            vid = videographer.make_reel(
                lst, dossier, idea, gen, work,
                asset_index=indexes[lid],
                target_seconds=args.target_seconds,
                xdur=args.xdur,
                max_frames=args.max_frames,
                best_of_n=args.best_of_n,
                ensure_ready=lambda: _ensure_ready(gen),
            )
            if vid:
                published += 1
        except Exception as exc:  # noqa: BLE001
            brand.log_activity(lid, "⚠️", f"Campaign “{idea.get('theme','?')}” hit an error", "generate")
            print(f"  ! campaign failed: {exc}")

        # Long-horizon hygiene: keep the dossier bounded, prune old cuts off disk,
        # and run a weekly reflection (no-op until its cadence elapses).
        try:
            brand.compact(lid)
            pruned = listings.prune_versions(lid)
            if pruned:
                print(f"  · pruned {pruned} old cut(s) from disk")
            if brand.reflect(lid):
                print(f"  · weekly reflection written for {lid}")
        except Exception as exc:  # noqa: BLE001
            print(f"  ! hygiene pass failed ({exc})")

        if args.sleep > 0 and published < args.max_videos:
            time.sleep(args.sleep)

    print(f"\ndone — published {published} cut(s) across {len(projects)} project(s)")


if __name__ == "__main__":
    main()
