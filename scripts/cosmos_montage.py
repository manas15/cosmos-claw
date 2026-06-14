"""Terminal-driven Cosmos montage.

For each photo: GPT-4o (vision) looks at it, says what the space is, and writes a
short embodied first-person motion prompt; Cosmos turns it into a SHORT clip;
then everything is stitched with fast, varied cross-transitions into one montage.

Run on a live Cosmos endpoint (LIVEHERE_BACKEND=cosmos + tunnel up):
  .venv/bin/python scripts/cosmos_montage.py --photos "uploads/listing_la-house-1/photo_0*.jpg"
Dry-run the transitions without a GPU (uses the local stub + heuristic labels):
  .venv/bin/python scripts/cosmos_montage.py --backend stub --no-vision --frames 49

Key flags:
  --photos GLOB     photos to use (quote the glob)
  --listing ID      use a listing folder instead of --photos
  --out PATH        output mp4 (default uploads/montage.mp4)
  --frames N        Cosmos frames per clip (short! default 49 ≈ 2s @24fps)
  --xdur SEC        transition duration (default 0.35, fast)
  --backend B       cosmos | stub  (default: from .env)
  --no-vision       skip GPT-4o; use a generic walk-in prompt
  --no-music        don't add a music bed
"""

from __future__ import annotations

import argparse
import glob as globlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, transitions
from app.audio import mux_audio, synth_music_bed
from app.director import _encode_image
from app.ffmpeg_utils import probe_duration
from app.generation.base import Scene
from app.generation.factory import get_generator

OUT_DEFAULT = "uploads/montage.mp4"

_VISION_SYSTEM = (
    "You are a cinematographer using NVIDIA Cosmos, a world-model video generator "
    "that excels at embodied, first-person forward motion through real spaces. "
    "Look at ONE photo of a rental space and return STRICT JSON: "
    '{"label": "2-4 word name of the space", '
    '"shot": one of [walk in, walk forward, step inside, approach and enter, '
    'dolly-in with parallax, slow arc], '
    '"prompt": "a short (1-2 sentence) image-to-video instruction for a smooth, '
    'gimbal-stabilized FIRST-PERSON POV that moves forward into THIS exact space '
    'at a natural pace, with real parallax and depth. Keep the room identical to '
    'the photo; no people in frame (the viewer is the camera); natural light."}'
)

_GENERIC_SHOTS = ["approach and enter", "walk in", "walk forward", "step inside", "slow arc"]


def _gather_photos(args) -> list[str]:
    if args.listing:
        d = config.UPLOAD_DIR / f"listing_{args.listing}"
        photos = sorted(str(p) for p in d.glob("photo_*.jpg"))
        if photos:
            return photos
        # fall back to the listings folder originals
        from app import listings as L

        lst = L.get_listing(args.listing)
        if lst:
            return [str(L.thumb(lst, i)) for i in range(len(lst.photos))]
        return []
    if args.photos:
        return sorted(globlib.glob(args.photos))
    return []


def _analyze(path: str, work: Path, idx: int, use_vision: bool) -> dict:
    """Return {label, shot, prompt} for a photo (GPT-4o vision or a fallback)."""
    fallback = {
        "label": f"Space {idx + 1}",
        "shot": _GENERIC_SHOTS[idx % len(_GENERIC_SHOTS)],
        "prompt": (
            "Smooth, gimbal-stabilized first-person POV that walks forward into "
            "this exact space at a natural pace, real parallax and depth. Keep the "
            "room identical to the photo; no people in frame; natural light."
        ),
    }
    if not use_vision or not config.OPENAI_API_KEY:
        return fallback
    uri = _encode_image(path, work, idx)
    if not uri:
        return fallback
    try:
        from openai import OpenAI

        client = OpenAI(api_key=config.OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            response_format={"type": "json_object"},
            temperature=0.5,
            max_tokens=300,
            messages=[
                {"role": "system", "content": _VISION_SYSTEM},
                {"role": "user", "content": [
                    {"type": "text", "text": "Look at this space and return the JSON."},
                    {"type": "image_url", "image_url": {"url": uri, "detail": "low"}},
                ]},
            ],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        return {
            "label": str(data.get("label") or fallback["label"])[:40],
            "shot": str(data.get("shot") or fallback["shot"]),
            "prompt": str(data.get("prompt") or fallback["prompt"]),
        }
    except Exception as exc:  # noqa: BLE001
        print(f"  ! vision failed ({exc}); using generic walk-in")
        return fallback


def main() -> None:
    ap = argparse.ArgumentParser(description="Cosmos per-photo montage")
    ap.add_argument("--photos", default="")
    ap.add_argument("--listing", default="")
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--frames", type=int, default=49, help="Cosmos frames/clip (short)")
    ap.add_argument("--xdur", type=float, default=0.35, help="transition seconds")
    ap.add_argument("--backend", default="", help="cosmos | stub (default from .env)")
    ap.add_argument("--no-vision", action="store_true")
    ap.add_argument("--no-music", action="store_true")
    args = ap.parse_args()

    photos = _gather_photos(args)
    if not photos:
        raise SystemExit("no photos found (use --photos GLOB or --listing ID)")

    # Short clips: the montage feel comes from many quick beats + fast cuts.
    config.COSMOS_NUM_FRAMES = args.frames
    clip_seconds = max(1.0, args.frames / float(config.COSMOS_FPS))  # keep stub + cosmos in sync

    gen = get_generator(args.backend or None)
    ok, why = gen.available()
    print(f"backend: {gen.name} | ready: {ok} | {why}")
    print(f"frames/clip={config.COSMOS_NUM_FRAMES}  transition={args.xdur}s  photos={len(photos)}")
    if not ok:
        raise SystemExit("generation backend not ready (Cosmos tunnel up? .env=cosmos?)")

    work = config.UPLOAD_DIR / "_montage"
    work.mkdir(parents=True, exist_ok=True)
    clips: list[str] = []
    for i, photo in enumerate(photos):
        info = _analyze(photo, work, i, use_vision=not args.no_vision)
        print(f"[{i + 1}/{len(photos)}] {Path(photo).name} → {info['label']!r} · {info['shot']}")
        scene = Scene(
            index=i, source_path=photo, prompt=info["prompt"], caption="",
            time_label="", time_of_day="day", duration=clip_seconds,
            shot=info["shot"], motion_strength=0.85,
        )
        raw = str(work / f"clip_{i:02d}.mp4")
        try:
            gen.generate_clip(scene, raw)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! generation failed: {exc}")
            continue
        clips.append(raw)

    if not clips:
        raise SystemExit("no clips were generated")

    print(f"stitching {len(clips)} clips with fast transitions…")
    silent = str(work / "montage_silent.mp4")
    transitions.xfade_montage(clips, silent, work, transition_dur=args.xdur)

    out = args.out
    if args.no_music:
        Path(out).write_bytes(Path(silent).read_bytes())
    else:
        dur = probe_duration(silent) or 0.0
        try:
            music = synth_music_bed(str(work / "music.wav"), dur, mood="uplifting")
            mux_audio(silent, out, voiceover=None, music=music)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! music/mux failed ({exc}); using silent montage")
            Path(out).write_bytes(Path(silent).read_bytes())

    print(f"\ndone → {out}  ({probe_duration(out):.1f}s, {len(clips)} clips)")


if __name__ == "__main__":
    main()
