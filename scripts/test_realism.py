"""Generate ONE real Cosmos clip and dump frames to eyeball fidelity vs source."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config
from app.ffmpeg_utils import run_ffmpeg
from app.generation.base import Scene
from app.generation.cosmos import CosmosClipGenerator

SRC = "uploads/listing_la-house-1/photo_04.jpg"
OUT = "uploads/realism_test.mp4"


def main() -> None:
    gen = CosmosClipGenerator()
    ok, why = gen.available()
    print("backend:", gen.name, "| ready:", ok, "|", why)
    print(f"steps={config.COSMOS_STEPS} guidance={config.COSMOS_GUIDANCE} "
          f"flow_shift={config.COSMOS_FLOW_SHIFT} frames={config.COSMOS_NUM_FRAMES}")
    if not ok:
        raise SystemExit("cosmos backend not ready (is the SSH tunnel up?)")

    scene = Scene(
        index=0,
        source_path=SRC,
        prompt=(
            "Real estate b-roll of this private patio with terracotta tiles, blue "
            "chairs and a flowering bush; the camera very slowly pans a little. "
            "Natural daylight."
        ),
        caption="",
        time_label="",
        time_of_day="day",
        duration=config.SCENE_DURATION,
    )
    print("generating (this can take ~2 min at 50 steps)…")
    gen.generate_clip(scene, OUT)

    for t, name in [(0.1, "f_start"), (2.0, "f_mid"), (3.8, "f_end")]:
        run_ffmpeg(["-ss", str(t), "-i", OUT, "-frames:v", "1", f"/tmp/bnb/{name}.png"])
    print("done:", OUT)


if __name__ == "__main__":
    main()
