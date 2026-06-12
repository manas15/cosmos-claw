"""End-to-end orchestration: inputs -> storyboard -> clips -> final MP4."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Callable

from . import assembly, config
from .audio import mux_audio, synth_music_bed, tts_voiceover
from .decorate import decorate_clip, decorate_trailer_clip
from .director import build_director_storyboard
from .ffmpeg_utils import extract_last_frame, probe_duration
from .generation.base import ClipGenerator
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
) -> dict:
    """Run the full pipeline. Returns metadata incl. the final video path."""
    ready, reason = generator.available()
    if not ready:
        raise RuntimeError(f"Generation backend '{generator.name}' not ready: {reason}")

    mode = (mode or config.DEFAULT_MODE).lower()
    if mode == "trailer":
        return _generate_trailer(
            job_dir, media_paths, instructions, address, lease, generator,
            on_progress, include=include,
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
) -> dict:
    """Vertical (9:16) listing trailer: curated b-roll + music + AI voiceover."""
    work = job_dir / "work"
    work.mkdir(parents=True, exist_ok=True)

    if on_progress:
        on_progress(0, 1, "Scouting your best shots…")

    # GPT-4o picks/orders shots, writes captions + a voiceover script + end card,
    # and extracts location/price/rating/POIs for the interleaved info cards.
    plan = build_trailer_plan(
        media_paths, instructions, address, lease, work_dir=work, include=include
    )
    scenes = plan.scenes
    items = plan.items

    total = len(items) + 1  # +1 for the end card
    if on_progress:
        on_progress(0, total, f"Filming {len(scenes)} shots…")

    clip_paths: list[str] = []
    for n, item in enumerate(items):
        if item.kind == "room" and item.scene is not None:
            scene = item.scene
            raw_path = str(work / f"raw_{scene.index:02d}.mp4")
            generator.generate_clip(scene, raw_path)
            clip_path = str(work / f"clip_{scene.index:02d}.mp4")
            decorate_trailer_clip(raw_path, clip_path, scene, title=item.title)
            clip_paths.append(clip_path)
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

        if on_progress:
            on_progress(len(clip_paths), total, label)

    end_card = str(work / "end_card.mp4")
    assembly.render_trailer_end_card(work, end_card, end_card=plan.end_card or {"name": plan.title})
    clip_paths.append(end_card)
    if on_progress:
        on_progress(total, total, "Scoring & narrating…")

    silent_path = work / "trailer_silent.mp4"
    assembly.concat_clips(clip_paths, str(silent_path), work)

    # Layer a soft music bed + AI voiceover over the finished cut.
    video_dur = probe_duration(str(silent_path)) or (config.SCENE_DURATION * len(scenes))
    music_path: str | None = config.MUSIC_PATH or None
    if not music_path:
        try:
            music_path = synth_music_bed(str(work / "music.wav"), video_dur)
        except Exception as exc:  # noqa: BLE001
            print(f"[pipeline] music synth failed ({exc}); continuing without music")
            music_path = None

    vo_path = tts_voiceover(plan.voiceover, str(work / "vo.mp3")) if plan.voiceover else None

    final_path = job_dir / "listing_trailer.mp4"
    try:
        mux_audio(str(silent_path), str(final_path), voiceover=vo_path, music=music_path)
    except Exception as exc:  # noqa: BLE001
        print(f"[pipeline] audio mux failed ({exc}); using silent cut")
        final_path = silent_path

    return {
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
    }
