"""The Videographer skill: turn a campaign idea + photos into a posted reel.

This is the single canonical generator the Doer agent runs. Given a project, a
campaign ``idea`` (which photos in what order, format, music, voice, caption,
voiceover) and an ``asset_index`` (per-photo label/shot/prompt from vision), it:

  FILM  -> short embodied clips on the active video backend (best-of-N optional)
  CUT   -> cross-fade the clips into the campaign's aspect ratio
  VOICE -> synthesize the GPT voiceover + a mood music bed and mix them in
  PUBLISH -> install the finished cut as a listing version (poster + caption +
             handle + audio + status) so it lands in the Agent Loop feed.

The marketing loop, the manual montage CLI, and the web app all call
``make_reel`` so there is exactly one implementation of the generation path,
and any ``ClipGenerator`` backend (small Cosmos, a bigger model, a hosted API)
plugs in unchanged.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Callable

from . import brand, config, curation, listings, transitions
from .audio import mux_audio, synth_music_bed, tts_voiceover
from .ffmpeg_utils import extract_poster, probe_duration
from .generation.base import Scene

ProgressFn = Callable[[int, int, str], None]
ReadyFn = Callable[[], bool]


def auto_frames(n_clips: int, target_seconds: float, xdur: float, max_frames: int = 49) -> int:
    """Frames/clip so the stitched cut lands near ``target_seconds``.

    ``max_frames`` caps per-clip length: shorter clips = smaller MP4 responses
    that transfer reliably over a jittery tunnel. More, shorter beats keep the
    montage in the 20-30s range without huge payloads.
    """
    fps = float(config.COSMOS_FPS)
    target = min(30.0, max(18.0, target_seconds))
    per_clip = (target + (n_clips - 1) * xdur) / max(1, n_clips)
    per_clip = min(3.0, max(1.4, per_clip))
    frames = int(round((per_clip * fps - 1) / 4.0)) * 4 + 1  # snap to 4k+1 for Cosmos
    return max(25, min(int(max_frames), frames))


def _film_beat(gen, scene: Scene, raw: str, *, best_of_n: int, work: Path) -> str | None:
    """Render one beat (best-of-N variants, auto-pick the steadiest). None on fail."""
    takes: list[str] = []
    for variant in range(max(1, best_of_n)):
        out = raw if best_of_n <= 1 else str(Path(raw).with_suffix("")) + f"_t{variant}.mp4"
        ok = False
        for attempt in range(2):  # one retry covers a transient tunnel blip
            try:
                gen.generate_clip(scene, out, variant=variant)
                ok = True
                break
            except Exception as exc:  # noqa: BLE001
                print(f"    ! beat {scene.index} variant {variant} attempt {attempt + 1} failed: {exc}")
                time.sleep(2.0)
        if ok:
            takes.append(out)
    if not takes:
        return None
    if len(takes) == 1:
        return takes[0]
    best_i, _ = curation.pick_best(takes, work)
    return takes[best_i]


def make_reel(
    listing: listings.Listing,
    dossier: dict,
    idea: dict,
    gen,
    work: Path,
    *,
    asset_index: list[dict],
    target_seconds: float = 24.0,
    xdur: float = 0.35,
    max_frames: int = 49,
    best_of_n: int = 1,
    on_progress: ProgressFn | None = None,
    ensure_ready: ReadyFn | None = None,
    source: str = "videographer",
) -> str | None:
    """Film + cut + voice + publish one reel. Returns the new version id, or None."""
    lid = listing.id
    fmt = idea.get("format") or config.DEFAULT_FORMAT
    preset = config.FORMAT_PRESETS.get(fmt) or config.FORMAT_PRESETS[config.DEFAULT_FORMAT]
    label, ratio = preset["label"], preset["ratio"]
    size = (preset["w"], preset["h"])

    by_index = {a["index"]: a for a in asset_index}
    chosen = [by_index[i] for i in idea.get("photo_indices", []) if i in by_index]
    if len(chosen) < 2:
        print("  ! not enough photos for this idea; skipping")
        return None

    frames = auto_frames(len(chosen), target_seconds, xdur, max_frames)
    config.COSMOS_NUM_FRAMES = frames
    clip_seconds = max(1.0, frames / float(config.COSMOS_FPS))

    total = len(chosen)
    brand.log_activity(lid, "🎬", f"Filming {label} ({ratio}) · {total} beats", "generate")
    print(f"  ▸ “{idea.get('theme', 'cut')}” → {label} {ratio} · {total} beats · {frames}f/clip")

    clips: list[str] = []
    for slot, a in enumerate(chosen):
        if ensure_ready is not None and not ensure_ready():
            print("  ! endpoint stayed down too long; cutting this shoot short")
            break
        if on_progress:
            on_progress(slot, total, f"Filming beat {slot + 1}/{total}: {a.get('label', '')}")
        scene = Scene(
            index=slot, source_path=a["path"], prompt=a["prompt"], caption="",
            time_label="", time_of_day="day", duration=clip_seconds,
            shot=a.get("shot", "walk forward"), motion_strength=0.85,
        )
        raw = str(work / f"clip_{slot:02d}.mp4")
        picked = _film_beat(gen, scene, raw, best_of_n=best_of_n, work=work)
        if picked:
            clips.append(picked)
            print(f"    · beat {slot + 1}/{total}: {a.get('label', '')!r} ✓")

    if len(clips) < 2:
        brand.log_activity(lid, "⚠️", f"Shoot fell short for “{idea.get('theme', '?')}” — retrying later", "generate")
        print("  ! too few clips rendered; skipping publish")
        return None

    # CUT: cross-fade into the campaign's aspect ratio.
    if on_progress:
        on_progress(total, total, "Cutting + scoring the montage")
    silent = str(work / "campaign_silent.mp4")
    transitions.xfade_montage(clips, silent, work, transition_dur=xdur, size=size)
    dur = probe_duration(silent) or clip_seconds * len(clips)

    # VOICE + music.
    voice = idea.get("voice") or (dossier.get("brand") or {}).get("voice") or config.TTS_VOICE
    music_mood = idea.get("music") or (dossier.get("brand") or {}).get("music") or "uplifting"
    brand.log_activity(lid, "🎙️", f"Recording an AI voiceover ({voice})", "audio")
    vo = None
    if idea.get("voiceover"):
        vo = tts_voiceover(idea["voiceover"], str(work / "voice.mp3"), voice=voice)
    try:
        music = synth_music_bed(str(work / "music.wav"), dur, mood=music_mood)
    except Exception as exc:  # noqa: BLE001
        print(f"    ! music synth failed ({exc})")
        music = None

    out = str(work / "campaign_final.mp4")
    try:
        mux_audio(silent, out, voiceover=vo, music=music)
    except Exception as exc:  # noqa: BLE001
        print(f"    ! mux failed ({exc}); using silent cut")
        Path(out).write_bytes(Path(silent).read_bytes())

    return _publish(listing, dossier, idea, out, len(clips), preset, dur, source=source)


def _publish(
    listing: listings.Listing,
    dossier: dict,
    idea: dict,
    out_path: str,
    scene_count: int,
    preset: dict,
    dur: float,
    *,
    source: str,
) -> str:
    """Install the finished cut as a listing version → shows in the Agent Loop."""
    lid = listing.id
    label, ratio = preset["label"], preset["ratio"]
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
    post = brand.build_post(dossier, label)
    caption = idea.get("caption") or post["caption"]
    hashtags = idea.get("hashtags") or post["hashtags"]
    listings.save_version_meta(
        lid, vid,
        {
            "name": listing.name,
            "title": idea.get("theme") or post.get("handle") or listing.name,
            "location": facts.get("location") or "",
            "price": facts.get("price") or "",
            "scene_count": scene_count,
            "info_card_count": 0,
            "format": idea.get("format") or config.DEFAULT_FORMAT,
            "format_label": label,
            "ratio": ratio,
            "voice": idea.get("voice") or (dossier.get("brand") or {}).get("voice") or config.TTS_VOICE,
            "music": idea.get("music") or (dossier.get("brand") or {}).get("music") or "uplifting",
            "handle": post["handle"],
            "caption": caption,
            "hashtags": hashtags,
            "voiceover": idea.get("voiceover", ""),
            "angle": idea.get("angle", ""),
            "best_of_n": 1,
            "has_takes": False,
            "takes": [],
            "created_at": created_at,
            "source": source,
            # Feedback lifecycle (Phase 3): every fresh cut awaits a human decision.
            "status": "pending_review",
            "posted_at": None,
            "review_due": None,
            "slop_notes": "",
            "performance": None,
        },
    )

    # Remember the campaign so future ideas stay fresh + on-strategy.
    dossier = brand.load(lid) or dossier
    dossier.setdefault("campaigns", []).append({
        "ts": created_at, "vid": vid, "theme": idea.get("theme", ""),
        "format": idea.get("format", ""), "music": idea.get("music", ""),
        "voice": idea.get("voice", ""),
    })
    brand.save(lid, dossier)

    brand.log_activity(lid, "✅", f"Published “{idea.get('theme', 'a cut')}” — {label} {ratio} with voiceover", "publish")
    print(f"  ✓ published {lid} v{vid} ({label} {ratio}) → Agent Loop")
    return vid
