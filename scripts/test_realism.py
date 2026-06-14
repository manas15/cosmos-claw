"""Cosmos motion/realism PARAM SWEEP harness.

Renders one Cosmos clip per (flow_shift x guidance x num_frames x prompt-style)
combo on a reference photo, grabs a mid frame from each, and tiles them into a
single labeled contact sheet so you can eyeball the cinematic-but-real sweet
spot. Once you pick a winner, lock its values into app/config.py / .env.

Requires the Cosmos backend to be reachable (SSH tunnel up + .env -> cosmos).
Use --dry-run to print the grid without spending GPU time.

Examples:
  python scripts/test_realism.py --dry-run
  python scripts/test_realism.py --flow 7,10,13 --guidance 5 --frames 97
  python scripts/test_realism.py --photo uploads/listing_la-house-1/photo_04.jpg \
      --flow 8,11 --guidance 4,5 --frames 97 --styles deliberate,ambient
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config
from app.ffmpeg_utils import extract_frame, run_ffmpeg
from app.generation.base import Scene
from app.generation.cosmos import CosmosClipGenerator

DEFAULT_PHOTO = "uploads/listing_la-house-1/photo_04.jpg"
SWEEP_DIR = Path("uploads/sweep")

# Prompt styles to A/B. Each is a full image-to-video instruction; the fidelity
# suffix from config is appended by the generator.
PROMPT_STYLES = {
    "deliberate": (
        "Real estate b-roll of this space; the camera makes a smooth, deliberate "
        "cinematic dolly push-in with real parallax and depth. Natural daylight."
    ),
    "ambient": (
        "Real estate b-roll of this space; the camera makes a smooth, deliberate "
        "dolly push-in with real parallax while sheer curtains sway gently and "
        "warm daylight shifts. Natural light."
    ),
    "subtle": (
        "Real estate b-roll of this space; the camera slowly and gently pushes in "
        "a little. Natural daylight, steady and calm."
    ),
}


def _floats(s: str) -> list[float]:
    return [float(x) for x in s.split(",") if x.strip()]


def _ints(s: str) -> list[int]:
    return [int(x) for x in s.split(",") if x.strip()]


def _label_frame(src_png: str, out_png: str, label: str) -> None:
    """Burn a parameter label onto a frame (used to build the contact sheet)."""
    safe = label.replace(":", r"\:").replace("'", "")
    font = f"fontfile={config.FONT_PATH}:" if config.FONT_PATH else ""
    vf = (
        "scale=480:-2,"
        f"drawtext={font}text='{safe}':x=12:y=12:fontsize=24:fontcolor=white:"
        "box=1:boxcolor=black@0.6:boxborderw=8"
    )
    run_ffmpeg(["-i", src_png, "-vf", vf, "-frames:v", "1", out_png])


def _contact_sheet(labeled: list[str], out_png: str) -> None:
    if not labeled:
        return
    n = len(labeled)
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    # Renumber into a contiguous sequence ffmpeg's image2 reader can tile.
    seq_dir = Path(out_png).parent / "_seq"
    seq_dir.mkdir(parents=True, exist_ok=True)
    for i, p in enumerate(labeled):
        (seq_dir / f"s_{i:03d}.png").write_bytes(Path(p).read_bytes())
    run_ffmpeg(
        [
            "-framerate", "1",
            "-i", str(seq_dir / "s_%03d.png"),
            "-frames:v", "1",
            "-vf", f"tile={cols}x{rows}:padding=8:margin=8:color=0x11151d",
            out_png,
        ]
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Cosmos motion param sweep")
    ap.add_argument("--photo", default=DEFAULT_PHOTO)
    ap.add_argument("--flow", type=_floats, default=_floats("7,10,13"),
                    help="flow_shift values (comma-separated)")
    ap.add_argument("--guidance", type=_floats, default=_floats("5"),
                    help="guidance_scale values")
    ap.add_argument("--frames", type=_ints, default=_ints("97"),
                    help="num_output_frames values")
    ap.add_argument("--styles", default="deliberate",
                    help="prompt styles: " + ",".join(PROMPT_STYLES))
    ap.add_argument("--dry-run", action="store_true",
                    help="print the grid without generating")
    args = ap.parse_args()

    styles = [s.strip() for s in args.styles.split(",") if s.strip() in PROMPT_STYLES]
    if not styles:
        raise SystemExit(f"no valid styles; choose from {list(PROMPT_STYLES)}")

    grid = [
        (fs, g, fr, st)
        for st in styles
        for fr in args.frames
        for g in args.guidance
        for fs in args.flow
    ]
    print(f"sweep grid: {len(grid)} combos "
          f"(flow={args.flow} x guidance={args.guidance} x frames={args.frames} "
          f"x styles={styles}) on {args.photo}")
    for fs, g, fr, st in grid:
        print(f"  flow={fs} guidance={g} frames={fr} style={st}")
    if args.dry_run:
        return

    gen = CosmosClipGenerator()
    ok, why = gen.available()
    print("backend:", gen.name, "| ready:", ok, "|", why)
    if not ok:
        raise SystemExit("cosmos backend not ready (is the SSH tunnel up + .env=cosmos?)")

    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    labeled: list[str] = []
    for i, (fs, g, fr, st) in enumerate(grid):
        # The generator reads these from config at call time; motion_strength=0.5
        # makes _flow_shift() pass the base flow_shift straight through.
        config.COSMOS_FLOW_SHIFT = fs
        config.COSMOS_GUIDANCE = g
        config.COSMOS_NUM_FRAMES = fr

        out = SWEEP_DIR / f"clip_{i:03d}.mp4"
        scene = Scene(
            index=0, source_path=args.photo, prompt=PROMPT_STYLES[st],
            caption="", time_label="", time_of_day="day",
            duration=config.SCENE_DURATION, shot="slow push-in", motion_strength=0.5,
        )
        label = f"fs{fs} g{g} f{fr} {st}"
        print(f"[{i + 1}/{len(grid)}] generating {label} …")
        try:
            gen.generate_clip(scene, str(out), variant=0)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! failed: {exc}")
            continue

        frame = str(SWEEP_DIR / f"frame_{i:03d}.png")
        extract_frame(str(out), frame, at_seconds=max(0.5, (fr / config.COSMOS_FPS) * 0.5))
        lab = str(SWEEP_DIR / f"label_{i:03d}.png")
        _label_frame(frame, lab, label)
        labeled.append(lab)

    sheet = str(SWEEP_DIR / "contact_sheet.png")
    _contact_sheet(labeled, sheet)
    print(f"\ndone. contact sheet: {sheet}\nclips in: {SWEEP_DIR}/")


if __name__ == "__main__":
    main()
