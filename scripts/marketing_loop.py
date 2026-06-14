"""Always-on marketing-manager loop for Cosmos Claw.

Thinks like a social-media manager for each project and keeps the Agent Loop
busy with fresh, ready-to-post cuts:

  1. STUDY  — GPT-4o (vision) labels every uploaded photo once (cached in the
              brand dossier as the asset index: what each space is + a motion
              prompt Cosmos can film).
  2. IDEATE — GPT-4o brainstorms ONE fresh campaign distinct from past themes:
              an angle, which photos (in order), the social format, a music
              mood, a TTS voice, a ready-to-post caption + hashtags, and a
              ~25s spoken VOICEOVER script.
  3. FILM   — Cosmos turns the chosen photos into short embodied clips.
  4. CUT    — clips are cross-faded into the campaign's aspect ratio, then the
              GPT voiceover is mixed over a mood-matched music bed.
  5. PUBLISH— the finished cut is installed as a listing version (poster +
              caption + handle + recommended audio) so it lands in the Agent
              Loop feed, and every step is written to the dossier's activity
              timeline.

It loops, alternating projects, until --max-videos cuts are published. Each
campaign is independent: one failure is logged and the loop moves on. Grounds
every idea on the project's locked-in brand facts so made-up details stay
consistent across videos.

Run (live Cosmos endpoint + tunnel up):
  .venv/bin/python scripts/marketing_loop.py --max-videos 6
Dry-run without a GPU:
  .venv/bin/python scripts/marketing_loop.py --backend stub --max-videos 2
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import brand, config, listings, marketing_agent, transitions
from app.audio import mux_audio, synth_music_bed, tts_voiceover
from app.ffmpeg_utils import extract_poster, probe_duration
from app.generation.base import Scene
from app.generation.factory import get_generator

# Import the vision analyzer the montage script already uses.
from scripts.cosmos_montage import _analyze  # noqa: E402

_VOICES = ("alloy", "echo", "fable", "onyx", "nova", "shimmer")
_MOODS = ("warm", "calm", "uplifting", "energetic", "luxury", "moody")
_FORMATS = tuple(config.FORMAT_PRESETS.keys())

_DEFAULT_PROJECTS = ["la-house-1", "hacker-house"]


# --- the manager's brain ------------------------------------------------

_IDEA_SYSTEM = (
    "You are the always-on social-media MARKETING MANAGER for a single venue. "
    "You own its social handle and ship short-form video constantly. Brainstorm "
    "ONE fresh campaign for the next post that is clearly DIFFERENT from the past "
    "themes you are given (different angle, hook, room mix, format, and energy). "
    "Ground every claim ONLY on the brand facts provided — never contradict them. "
    "Pick photos by their index from the asset list so the cut tells a little "
    "story. Write a punchy, ready-to-post caption (no hashtags inside it) and a "
    "spoken VOICEOVER script of about 55-70 words (~25 seconds) that a narrator "
    "reads over the video: a hook, 2-3 quick selling beats, and a call to action. "
    "Return STRICT JSON only with keys: theme, angle, format, music, voice, "
    "photo_indices, caption, voiceover, hashtags."
)


def _campaign_idea(dossier: dict, asset_index: list[dict], past_themes: list[str],
                   target_seconds: float) -> dict:
    """Ask GPT-4o for one fresh campaign grounded on the dossier + assets."""
    facts = dossier.get("facts") or {}
    bnd = dossier.get("brand") or {}
    assets = [{"index": a["index"], "space": a.get("label", "")} for a in asset_index]
    user = {
        "brand": {
            "name": dossier.get("name"),
            "oneliner": bnd.get("oneliner", ""),
            "tone": bnd.get("tone", ""),
            "audience": bnd.get("audience", ""),
            "selling_points": bnd.get("selling_points", []),
        },
        "facts": {k: facts.get(k) for k in ("title", "location", "price", "summary",
                                            "bedrooms", "guests", "amenities", "nearby")},
        "available_photos": assets,
        "past_themes": past_themes[-12:],
        "allowed_formats": list(_FORMATS),
        "allowed_music_moods": list(_MOODS),
        "allowed_voices": list(_VOICES),
        "target_seconds": target_seconds,
        "want_photos": "pick 9 to 12 indices, ordered to tell a story (short clips, snappy cuts)",
    }
    import json as _json
    data = marketing_agent._gpt_json(_IDEA_SYSTEM, _json.dumps(user), max_tokens=900, temperature=0.85)
    return _sanitize_idea(data, asset_index, dossier)


def _sanitize_idea(data: dict, asset_index: list[dict], dossier: dict) -> dict:
    """Clamp the model's idea to valid choices, with sensible fallbacks."""
    n = len(asset_index)
    valid = {a["index"] for a in asset_index}

    # The model may hand back a list, or a "1, 3, 6" string — normalize both.
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
    idx = idx[:12]  # more, shorter beats → small reliable clips, still ~20-30s

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
    # Hashtags may arrive as a list or one "#a #b, #c" string — normalize both.
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


# --- the manager's hands ------------------------------------------------


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
    brand.log_activity(lst.id, "🧠", f"Studying the space — reviewing {len(lst.photos)} assets", "research")
    photos = _photos_for(lst)
    index: list[dict] = []
    for i, p in enumerate(photos):
        info = _analyze(p, work, i, use_vision=use_vision)
        index.append({"index": i, "path": p, "label": info["label"],
                      "shot": info["shot"], "prompt": info["prompt"]})
        print(f"   · asset {i + 1}/{len(photos)}: {info['label']!r}")
    dossier = brand.load(lst.id) or dossier
    dossier["asset_index"] = index
    brand.save(lst.id, dossier)
    brand.log_activity(lst.id, "🗂️", f"Built an asset index of {len(index)} spaces", "research")
    return index


def _auto_frames(n_clips: int, target_seconds: float, xdur: float, max_frames: int = 49) -> int:
    """Frames/clip so the stitched cut lands near target_seconds.

    ``max_frames`` caps the per-clip length: shorter clips = smaller (~5MB) MP4
    responses that transfer reliably over a jittery tunnel (Starlink), where big
    73-frame (~9MB) clips were breaking mid-download. More, shorter beats keep
    the montage in the 20-30s range without huge payloads.
    """
    fps = float(config.COSMOS_FPS)
    target = min(30.0, max(18.0, target_seconds))
    per_clip = (target + (n_clips - 1) * xdur) / max(1, n_clips)
    per_clip = min(3.0, max(1.4, per_clip))
    frames = int(round((per_clip * fps - 1) / 4.0)) * 4 + 1  # snap to 4k+1 for Cosmos
    return max(25, min(int(max_frames), frames))


def _run_campaign(lst: listings.Listing, dossier: dict, asset_index: list[dict],
                  idea: dict, gen, work: Path, *, target_seconds: float,
                  xdur: float, max_frames: int = 49) -> bool:
    """Film + cut + voice + publish one campaign. Returns True if published."""
    lid = lst.id
    preset = config.FORMAT_PRESETS[idea["format"]]
    label, ratio = preset["label"], preset["ratio"]
    size = (preset["w"], preset["h"])

    by_index = {a["index"]: a for a in asset_index}
    chosen = [by_index[i] for i in idea["photo_indices"] if i in by_index]
    if len(chosen) < 2:
        print("  ! not enough photos for this idea; skipping")
        return False

    frames = _auto_frames(len(chosen), target_seconds, xdur, max_frames)
    config.COSMOS_NUM_FRAMES = frames
    clip_seconds = max(1.0, frames / float(config.COSMOS_FPS))

    brand.log_activity(lid, "💡", f"New campaign idea — “{idea['theme']}”", "idea")
    if idea.get("angle"):
        brand.log_activity(lid, "📝", idea["angle"], "idea")
    brand.log_activity(lid, "🎬",
                       f"Filming {label} ({ratio}) · {len(chosen)} beats on live Cosmos", "generate")
    print(f"  ▸ “{idea['theme']}” → {label} {ratio} · {len(chosen)} beats · {frames}f/clip")

    clips: list[str] = []
    for slot, a in enumerate(chosen):
        # Wait out any tunnel/wifi blip BEFORE filming so we never burn a beat
        # against a dead endpoint. The keeper restores the tunnel underneath.
        if not _ensure_ready(gen):
            print("  ! endpoint stayed down too long; cutting this shoot short")
            break
        scene = Scene(
            index=slot, source_path=a["path"], prompt=a["prompt"], caption="",
            time_label="", time_of_day="day", duration=clip_seconds,
            shot=a.get("shot", "walk forward"), motion_strength=0.85,
        )
        raw = str(work / f"clip_{slot:02d}.mp4")
        ok = False
        for attempt in range(2):  # one retry covers a transient tunnel blip
            try:
                gen.generate_clip(scene, raw)
                ok = True
                break
            except Exception as exc:  # noqa: BLE001
                print(f"    ! clip {slot} attempt {attempt + 1} failed: {exc}")
                time.sleep(2.0)
        if ok:
            clips.append(raw)
            print(f"    · beat {slot + 1}/{len(chosen)}: {a['label']!r} ✓")

    if len(clips) < 2:
        brand.log_activity(lid, "⚠️", f"Shoot fell short for “{idea['theme']}” — retrying later", "generate")
        print("  ! too few clips rendered; skipping publish")
        return False

    # Cut: cross-fade into the campaign's aspect ratio.
    silent = str(work / "campaign_silent.mp4")
    transitions.xfade_montage(clips, silent, work, transition_dur=xdur, size=size)
    dur = probe_duration(silent) or clip_seconds * len(clips)

    # Voice + music.
    brand.log_activity(lid, "🎙️", f"Recording an AI voiceover ({idea['voice']})", "audio")
    vo = None
    if idea["voiceover"]:
        vo = tts_voiceover(idea["voiceover"], str(work / "voice.mp3"), voice=idea["voice"])
    try:
        music = synth_music_bed(str(work / "music.wav"), dur, mood=idea["music"])
    except Exception as exc:  # noqa: BLE001
        print(f"    ! music synth failed ({exc})")
        music = None

    out = str(work / "campaign_final.mp4")
    try:
        mux_audio(silent, out, voiceover=vo, music=music)
    except Exception as exc:  # noqa: BLE001
        print(f"    ! mux failed ({exc}); using silent cut")
        Path(out).write_bytes(Path(silent).read_bytes())

    # Publish: install as a listing version so it shows in the Agent Loop.
    _publish(lst, dossier, idea, out, len(clips), label, ratio, dur)
    return True


def _publish(lst: listings.Listing, dossier: dict, idea: dict, out_path: str,
             scene_count: int, label: str, ratio: str, dur: float) -> None:
    lid = lst.id
    vid = listings.new_version_id()
    created_at = time.time()
    vpath, ppath, _ = listings.version_paths(lid, vid)
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(out_path, vpath)
    try:
        extract_poster(str(vpath), str(ppath), at_seconds=max(1.0, dur * 0.2))
    except Exception as exc:  # noqa: BLE001
        print(f"    ! poster extraction failed: {exc}")

    facts = dossier.get("facts") or {}
    listings.save_version_meta(
        lid, vid,
        {
            "name": lst.name,
            "title": idea["theme"],
            "location": facts.get("location") or "",
            "price": facts.get("price") or "",
            "scene_count": scene_count,
            "info_card_count": 0,
            "format": idea["format"], "format_label": label, "ratio": ratio,
            "voice": idea["voice"], "music": idea["music"],
            "handle": brand.post_handle(dossier),
            "caption": idea["caption"], "hashtags": idea["hashtags"],
            "voiceover": idea["voiceover"],
            "angle": idea.get("angle", ""),
            "best_of_n": 1, "has_takes": False, "takes": [],
            "created_at": created_at,
            "source": "marketing_loop",
        },
    )

    # Remember the campaign so future ideas stay fresh.
    dossier = brand.load(lid) or dossier
    dossier.setdefault("campaigns", []).append({
        "ts": created_at, "vid": vid, "theme": idea["theme"],
        "format": idea["format"], "music": idea["music"], "voice": idea["voice"],
    })
    brand.save(lid, dossier)

    brand.log_activity(lid, "✅",
                       f"Published “{idea['theme']}” — {label} {ratio} with voiceover", "publish")
    print(f"  ✓ published {lid} v{vid} ({label} {ratio}) → Agent Loop")


def _endpoint_live(timeout: float = 8.0) -> bool:
    """A real liveness probe (GET /v1/models) — catches a dropped SSH tunnel
    that gen.available() (config-only) cannot see."""
    url = config.COSMOS_BASE_URL.rstrip("/") + "/models"
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=timeout) as r:
            return 200 <= getattr(r, "status", r.getcode()) < 300
    except Exception:
        return False


def _ensure_ready(gen, attempts: int = 60, wait: float = 20.0) -> bool:
    """Block until the backend is genuinely reachable; the keeper self-heals the
    tunnel underneath. For Cosmos we require a live probe so a wifi/tunnel blip
    PAUSES the loop instead of burning a campaign (~60*20s = up to 20 min)."""
    cosmos = "cosmos" in gen.name.lower()
    for i in range(attempts):
        ok, why = gen.available()
        if ok and (not cosmos or _endpoint_live()):
            return True
        if i == 0 or (i + 1) % 3 == 0:
            print(f"  … endpoint down — pausing for the tunnel to recover ({i + 1}/{attempts})")
        time.sleep(wait)
    return False


def main() -> None:
    ap = argparse.ArgumentParser(description="Always-on marketing-manager video loop")
    ap.add_argument("--projects", default=",".join(_DEFAULT_PROJECTS),
                    help="comma-separated listing ids to manage")
    ap.add_argument("--max-videos", type=int, default=6, help="total cuts to publish")
    ap.add_argument("--backend", default="", help="cosmos | stub (default from .env)")
    ap.add_argument("--target-seconds", type=float, default=24.0)
    ap.add_argument("--max-frames", type=int, default=49,
                    help="cap frames/clip so MP4s stay small + transfer reliably (~49 ≈ 2s)")
    ap.add_argument("--xdur", type=float, default=0.35, help="transition seconds")
    ap.add_argument("--sleep", type=float, default=0.0, help="pause between campaigns (s)")
    ap.add_argument("--no-vision", action="store_true", help="skip GPT vision asset labels")
    ap.add_argument("--reindex", action="store_true", help="rebuild the asset index")
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

    work = config.UPLOAD_DIR / "_mkt"
    work.mkdir(parents=True, exist_ok=True)

    # STUDY every project once.
    dossiers: dict[str, dict] = {}
    indexes: dict[str, list[dict]] = {}
    for lst in projects:
        d = brand.load_or_seed(lst)
        dossiers[lst.id] = d
        indexes[lst.id] = _asset_index(lst, d, work, use_vision=not args.no_vision,
                                        reindex=args.reindex)

    # IDEATE → FILM → PUBLISH, alternating projects.
    published = 0
    round_i = 0
    while published < args.max_videos:
        lst = projects[round_i % len(projects)]
        round_i += 1
        lid = lst.id
        print(f"\n=== {lst.name} ({lid}) · cut {published + 1}/{args.max_videos} ===")

        if not _ensure_ready(gen):
            print("  ! backend unavailable; stopping loop")
            break

        dossier = brand.load(lid) or dossiers[lid]
        past = [c.get("theme", "") for c in dossier.get("campaigns", [])]
        try:
            idea = _campaign_idea(dossier, indexes[lid], past, args.target_seconds)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! ideation failed ({exc}); using a generic idea")
            idea = _sanitize_idea({}, indexes[lid], dossier)

        try:
            if _run_campaign(lst, dossier, indexes[lid], idea, gen, work,
                             target_seconds=args.target_seconds, xdur=args.xdur,
                             max_frames=args.max_frames):
                published += 1
        except Exception as exc:  # noqa: BLE001
            brand.log_activity(lid, "⚠️", f"Campaign “{idea.get('theme','?')}” hit an error", "generate")
            print(f"  ! campaign failed: {exc}")

        if args.sleep > 0 and published < args.max_videos:
            time.sleep(args.sleep)

    print(f"\ndone — published {published} cut(s) across {len(projects)} project(s)")


if __name__ == "__main__":
    main()
