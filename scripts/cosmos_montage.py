"""Terminal montage CLI — a thin wrapper over the Videographer skill.

For each photo, GPT-4o (vision) writes a short embodied first-person motion
prompt; the active backend films a SHORT clip; the clips are cross-faded into
one montage, voiced + scored, and published as a listing version so the cut
lands in the Agent Loop feed. All of that lives in ``app.videographer.make_reel``
and ``app.vision`` — this file just gathers photos and calls it.

Live Cosmos endpoint (LIVEHERE_BACKEND=cosmos + tunnel up):
  .venv/bin/python scripts/cosmos_montage.py --listing la-house-1
Dry-run the transitions without a GPU (local stub + heuristic labels):
  .venv/bin/python scripts/cosmos_montage.py --listing la-house-1 --backend stub --no-vision
"""

from __future__ import annotations

import argparse
import glob as globlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import brand, config, listings, videographer
from app.generation.factory import get_generator
from app.vision import analyze


def _gather_photos(lst: listings.Listing | None, photos_glob: str) -> list[str]:
    if photos_glob:
        return sorted(globlib.glob(photos_glob))
    if lst is not None:
        out = []
        for i in range(len(lst.photos)):
            t = listings.thumb(lst, i)
            out.append(str(t) if t else str(lst.photos[i]))
        return out
    return []


def main() -> None:
    ap = argparse.ArgumentParser(description="Cosmos per-photo montage (thin CLI over the videographer)")
    ap.add_argument("--listing", required=True, help="listing id to film + publish into the Agent Loop")
    ap.add_argument("--photos", default="", help="optional glob to override which photos to use")
    ap.add_argument("--format", default=config.DEFAULT_FORMAT, help="reel | tiktok | ... (see FORMAT_PRESETS)")
    ap.add_argument("--target-seconds", type=float, default=26.0)
    ap.add_argument("--max-frames", type=int, default=49, help="cap frames/clip (~49 ≈ 2s)")
    ap.add_argument("--xdur", type=float, default=0.35, help="transition seconds")
    ap.add_argument("--best-of-n", type=int, default=int(config.COSMOS_BEST_OF_N))
    ap.add_argument("--music", default="uplifting", help="music mood (see audio moods)")
    ap.add_argument("--backend", default="", help="cosmos | stub | <dotted path> (default from .env)")
    ap.add_argument("--no-vision", action="store_true", help="skip GPT-4o; use a generic walk-in prompt")
    args = ap.parse_args()

    lst = listings.get_listing(args.listing)
    if lst is None:
        raise SystemExit(f"unknown listing '{args.listing}'")
    dossier = brand.load_or_seed(lst)

    photos = _gather_photos(lst, args.photos)
    if len(photos) < 2:
        raise SystemExit("need at least 2 photos (use --listing ID or --photos GLOB)")

    gen = get_generator(args.backend or None)
    ok, why = gen.available()
    print(f"backend: {gen.name} | ready: {ok} | {why}")
    if not ok:
        raise SystemExit("generation backend not ready (Cosmos tunnel up? .env=cosmos?)")

    work = config.UPLOAD_DIR / "_montage"
    work.mkdir(parents=True, exist_ok=True)
    context = (dossier.get("brand") or {}).get("oneliner") or dossier.get("use_case") or ""
    asset_index: list[dict] = []
    for i, photo in enumerate(photos):
        info = analyze(photo, work, i, use_vision=not args.no_vision, context=context)
        print(f"[{i + 1}/{len(photos)}] {Path(photo).name} → {info['label']!r} · {info['shot']}")
        asset_index.append({"index": i, "path": photo, **info})

    idea = {
        "theme": f"{lst.name} walkthrough",
        "angle": "",
        "format": args.format if args.format in config.FORMAT_PRESETS else config.DEFAULT_FORMAT,
        "music": args.music,
        "voice": (dossier.get("brand") or {}).get("voice") or config.TTS_VOICE,
        "photo_indices": [a["index"] for a in asset_index],
        "caption": "",
        "voiceover": "",
        "hashtags": [],
    }

    vid = videographer.make_reel(
        lst, dossier, idea, gen, work,
        asset_index=asset_index,
        target_seconds=args.target_seconds,
        xdur=args.xdur,
        max_frames=args.max_frames,
        best_of_n=args.best_of_n,
        source="cosmos_montage",
    )
    if vid:
        print(f"\ndone → published {args.listing} v{vid} (shows in the Agent Loop)")
    else:
        raise SystemExit("montage failed (see log above)")


if __name__ == "__main__":
    main()
