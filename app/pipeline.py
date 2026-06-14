"""End-to-end orchestration: inputs -> storyboard -> clips -> final MP4."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Callable

from . import assembly, config, curation
from .audio import mux_audio, synth_music_bed, tts_voiceover
from .decorate import decorate_clip, decorate_trailer_clip
from .director import build_director_storyboard
from .ffmpeg_utils import extract_last_frame, probe_duration
from .generation.base import ClipGenerator, Scene
from .infocards import make_info_card
from .trailer import build_trailer_plan

# Called as on_progress(done, total, label) after each beat so the UI can show
# live progress. ``done`` counts finished clips; ``total`` includes the end card.
ProgressFn = Callable[[int, int, str], None]


def generate_video(
    job_dir: Path,
    media_paths: list[str],
    instructions: str,
    address: str,
    lease: str,
    generator: ClipGenerator,
    on_progress: ProgressFn | None = None,
    mode: str | None = None,
    include: set[str] | None = None,
    brief: dict | None = None,
    grounding: str = "",
    voice: str | None = None,
    music: str | None = None,
    walkthrough: bool = False,
) -> dict:
    """Run the full pipeline. Returns metadata incl. the final video path."""
    ready, reason = generator.available()
    if not ready:
        raise RuntimeError(f"Generation backend '{generator.name}' not ready: {reason}")

    mode = (mode or config.DEFAULT_MODE).lower()
    if mode == "trailer":
        return _generate_trailer(
            job_dir, media_paths, instructions, address, lease, generator,
            on_progress, include=include, brief=brief, grounding=grounding,
            voice=voice, music=music, walkthrough=walkthrough,
        )

    work = job_dir / "work"
    work.mkdir(parents=True, exist_ok=True)

    # GPT-4o director plans a first-person POV day-in-the-life; falls back to the
    # simple morning/day/evening plan if no API key or on any error.
    scenes = build_director_storyboard(
        media_paths, instructions, address, lease, work_dir=work
    )

    total = len(scenes) + 1  # +1 for the end card
    if on_progress:
        on_progress(0, total, "Planning your day…")

    clip_paths: list[str] = []
    prev_raw: str | None = None
    for scene in scenes:
        # Chain continuous beats: seed this clip with the previous clip's last
        # frame so motion flows; otherwise start fresh from the room photo.
        if scene.continues_prev and prev_raw:
            chained = str(work / f"chain_{scene.index:02d}.png")
            try:
                scene.source_path = extract_last_frame(prev_raw, chained)
            except Exception as exc:  # noqa: BLE001
                print(f"[pipeline] last-frame chain failed ({exc}); using room photo")

        raw_path = str(work / f"raw_{scene.index:02d}.mp4")
        generator.generate_clip(scene, raw_path)
        prev_raw = raw_path

        # Finish every clip identically (captions, grade, fades, normalize).
        clip_path = str(work / f"clip_{scene.index:02d}.mp4")
        decorate_clip(raw_path, clip_path, scene)
        clip_paths.append(clip_path)

        if on_progress:
            label = f"{scene.time_label} · {scene.caption}".strip(" ·")
            on_progress(len(clip_paths), total, label)

    end_card = str(work / "end_card.mp4")
    assembly.render_end_card(work, end_card, address=address, lease=lease)
    clip_paths.append(end_card)
    if on_progress:
        on_progress(total, total, "Wrapping up…")

    final_path = job_dir / "day_in_the_life.mp4"
    assembly.concat_clips(clip_paths, str(final_path), work)

    return {
        "video_path": str(final_path),
        "video_file": final_path.name,
        "backend": generator.name,
        "scene_count": len(scenes),
        "scenes": [asdict(s) for s in scenes],
    }


def _generate_trailer(
    job_dir: Path,
    media_paths: list[str],
    instructions: str,
    address: str,
    lease: str,
    generator: ClipGenerator,
    on_progress: ProgressFn | None = None,
    include: set[str] | None = None,
    brief: dict | None = None,
    grounding: str = "",
    voice: str | None = None,
    music: str | None = None,
    walkthrough: bool = False,
) -> dict:
    """Vertical/landscape listing trailer: curated b-roll + music + AI voiceover."""
    work = job_dir / "work"
    work.mkdir(parents=True, exist_ok=True)

    if on_progress:
        on_progress(0, 1, "Scouting your best shots…" if not walkthrough
                    else "Planning the walkthrough…")

    # GPT-4o picks/orders shots, writes captions + a voiceover script + end card,
    # and extracts location/price/rating/POIs for the interleaved info cards.
    # When a marketing brief is present, its asset order + direction are honored.
    plan = build_trailer_plan(
        media_paths, instructions, address, lease, work_dir=work,
        include=include, brief=brief, grounding=grounding, walkthrough=walkthrough,
    )
    scenes = plan.scenes
    items = plan.items

    total = len(items) + 1  # +1 for the end card
    if on_progress:
        on_progress(0, total, f"Filming {len(scenes)} shots…")

    best_of_n = max(1, int(config.COSMOS_BEST_OF_N))

    clip_paths: list[str] = []
    # Ordered manifest so a different take can be re-stitched later without
    # re-running Cosmos. Room beats keep ALL takes; info/end cards are fixed.
    segments: list[dict] = []
    for n, item in enumerate(items):
        if item.kind == "room" and item.scene is not None:
            scene = item.scene
            chosen_clip, takes = _render_beat_takes(
                generator, scene, work, best_of_n, title=item.title
            )
            clip_paths.append(chosen_clip)
            segments.append(
                {
                    "type": "room",
                    "beat": scene.index,
                    "shot": scene.shot,
                    "caption": scene.caption,
                    "chosen": next((t["index"] for t in takes if t.get("chosen")), 0),
                    "takes": takes,
                }
            )
            label = scene.caption or "Filming…"
        else:
            # Info card: render a full-frame PNG, then a short static clip.
            png = str(work / f"info_{n:02d}.png")
            made = make_info_card(item.info_type, item.data, png)
            label = {"map": "Mapping the neighborhood…", "neighborhood": "Mapping the neighborhood…",
                     "price": "Adding the price…", "rating": "Adding the rating…"}.get(
                         item.info_type, "Adding details…")
            if made:
                clip_path = str(work / f"clip_info_{n:02d}.mp4")
                assembly.render_still_clip(made, clip_path, config.INFO_CARD_DURATION)
                clip_paths.append(clip_path)
                segments.append({"type": "clip", "clip": clip_path})

        if on_progress:
            on_progress(len(clip_paths), total, label)

    end_card = str(work / "end_card.mp4")
    assembly.render_trailer_end_card(work, end_card, end_card=plan.end_card or {"name": plan.title})
    clip_paths.append(end_card)
    segments.append({"type": "clip", "clip": end_card})
    if on_progress:
        on_progress(total, total, "Scoring & narrating…")

    silent_path = work / "trailer_silent.mp4"
    assembly.concat_clips(clip_paths, str(silent_path), work)

    # Layer a soft music bed + AI voiceover over the finished cut.
    video_dur = probe_duration(str(silent_path)) or (config.SCENE_DURATION * len(scenes))
    music_path: str | None = config.MUSIC_PATH or None
    music_mood = music or (brief or {}).get("music")
    if not music_path:
        try:
            music_path = synth_music_bed(str(work / "music.wav"), video_dur, mood=music_mood)
        except Exception as exc:  # noqa: BLE001
            print(f"[pipeline] music synth failed ({exc}); continuing without music")
            music_path = None

    vo_voice = voice or (brief or {}).get("voice")
    vo_path = (
        tts_voiceover(plan.voiceover, str(work / "vo.mp3"), voice=vo_voice)
        if plan.voiceover else None
    )

    final_path = job_dir / "listing_trailer.mp4"
    try:
        mux_audio(str(silent_path), str(final_path), voiceover=vo_path, music=music_path)
    except Exception as exc:  # noqa: BLE001
        print(f"[pipeline] audio mux failed ({exc}); using silent cut")
        final_path = silent_path

    result = {
        "video_path": str(final_path),
        "video_file": Path(final_path).name,
        "backend": generator.name,
        "scene_count": len(scenes),
        "scenes": [asdict(s) for s in scenes],
        "voiceover": plan.voiceover,
        "title": plan.title,
        "end_card": plan.end_card or {"name": plan.title},
        "location": plan.location,
        "price": plan.price,
        "info_card_count": len(items) - len(scenes),
        "mode": "trailer",
        "best_of_n": best_of_n,
    }
    # When we kept multiple takes, hand the manifest up so main.py can persist
    # the takes per version and offer the Studio take picker / re-stitch.
    if best_of_n > 1 and any(s["type"] == "room" and len(s.get("takes", [])) > 1 for s in segments):
        result["takes_manifest"] = {
            "segments": segments,
            "audio": {"music": music_path, "voiceover": vo_path},
        }
    return result


def _render_beat_takes(
    generator: ClipGenerator,
    scene: Scene,
    work: Path,
    best_of_n: int,
    title: str | None = None,
) -> tuple[str, list[dict]]:
    """Render N takes of one room beat, decorate each, auto-pick the best.

    Returns (chosen_decorated_clip, takes) where ``takes`` lists every take with
    its decorated clip path + motion/stability score; exactly one has chosen=True.
    """
    if best_of_n <= 1:
        raw_path = str(work / f"raw_{scene.index:02d}.mp4")
        generator.generate_clip(scene, raw_path)
        clip_path = str(work / f"clip_{scene.index:02d}.mp4")
        decorate_trailer_clip(raw_path, clip_path, scene, title=title)
        return clip_path, [
            {"index": 0, "clip": clip_path, "raw": raw_path, "chosen": True,
             "score": None, "motion": None, "smoothness": None}
        ]

    raw_paths: list[str] = []
    clip_paths: list[str] = []
    for k in range(best_of_n):
        raw_path = str(work / f"raw_{scene.index:02d}_t{k}.mp4")
        generator.generate_clip(scene, raw_path, variant=k)
        clip_path = str(work / f"clip_{scene.index:02d}_t{k}.mp4")
        decorate_trailer_clip(raw_path, clip_path, scene, title=title)
        raw_paths.append(raw_path)
        clip_paths.append(clip_path)

    best_idx, ranked = curation.pick_best(raw_paths, work)
    score_by_idx = {r["index"]: r for r in ranked}
    takes: list[dict] = []
    for k in range(best_of_n):
        m = score_by_idx.get(k, {})
        takes.append(
            {
                "index": k,
                "clip": clip_paths[k],
                "raw": raw_paths[k],
                "chosen": k == best_idx,
                "score": m.get("score"),
                "motion": m.get("motion"),
                "smoothness": m.get("smoothness"),
            }
        )
    print(
        f"[pipeline] beat {scene.index}: best-of-{best_of_n} -> take {best_idx} "
        f"(score {score_by_idx.get(best_idx, {}).get('score')})"
    )
    return clip_paths[best_idx], takes


def restitch_from_manifest(
    manifest: dict,
    picks: dict[int, int],
    out_path: str,
    work: Path,
) -> str:
    """Rebuild the final video from a persisted manifest using chosen takes.

    ``picks`` maps a room beat index -> take index. Beats not in ``picks`` keep
    their current ``chosen`` take. Re-concats + re-muxes the original audio.
    """
    work.mkdir(parents=True, exist_ok=True)
    clip_paths: list[str] = []
    for seg in manifest.get("segments", []):
        if seg.get("type") == "room":
            takes = seg.get("takes", [])
            beat = int(seg.get("beat", -1))
            want = picks.get(beat, seg.get("chosen", 0))
            chosen = next((t for t in takes if t.get("index") == want), None)
            if chosen is None and takes:
                chosen = takes[0]
            if chosen and chosen.get("clip"):
                clip_paths.append(chosen["clip"])
        elif seg.get("clip"):
            clip_paths.append(seg["clip"])

    if not clip_paths:
        raise RuntimeError("re-stitch manifest had no clips")

    silent = work / "restitch_silent.mp4"
    assembly.concat_clips(clip_paths, str(silent), work)

    audio = manifest.get("audio") or {}
    try:
        mux_audio(
            str(silent), out_path,
            voiceover=audio.get("voiceover") or None,
            music=audio.get("music") or None,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[pipeline] re-stitch mux failed ({exc}); using silent cut")
        Path(out_path).write_bytes(silent.read_bytes())
    return out_path
