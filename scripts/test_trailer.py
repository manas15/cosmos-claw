"""Stub-backed end-to-end test of the vertical listing-trailer pipeline."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config
from app.ffmpeg_utils import convert_image, probe_duration
from app.generation.stub import StubClipGenerator
from app.pipeline import generate_video

SRC = Path("/Users/manas/Documents/world-model-hack/Airbnbs/LA House 1")
JOB = config.UPLOAD_DIR / "test_trailer"

FACTS = (
    "Wiley's cottage — charming Mediterranean guesthouse in historic Hollywood. "
    "Superhost, 4.98 rating from 288 reviews, Top 5% of LA homes. Private red-tiled "
    "patio with gardens and lemon trees, French doors, separate living room. "
    "15-minute walk to Runyon Canyon; Trader Joe's, Whole Foods, coffee shops and "
    "restaurants nearby. Central AC, free street parking, dedicated workspace, fast wifi. "
    "Quiet, private, fenced backyard. Sleeps 3."
)


def main() -> None:
    if JOB.exists():
        shutil.rmtree(JOB)
    JOB.mkdir(parents=True)

    avifs = sorted(SRC.glob("*.avif"))[:8]
    media: list[str] = []
    for i, p in enumerate(avifs):
        out = JOB / f"input_{i:02d}.jpg"
        media.append(convert_image(str(p), str(out)))
    print(f"prepared {len(media)} photos")

    def on_progress(done: int, total: int, label: str) -> None:
        print(f"  [{done}/{total}] {label}")

    result = generate_video(
        job_dir=JOB,
        media_paths=media,
        instructions=FACTS,
        address="Wiley's cottage · Hollywood, LA",
        lease="$239 / night",
        generator=StubClipGenerator(),
        on_progress=on_progress,
        mode="trailer",
    )

    vp = result["video_path"]
    print("\n--- RESULT ---")
    print("video:", vp)
    print("duration:", round(probe_duration(vp), 2), "s")
    print("title:", result.get("title"))
    print("voiceover:", result.get("voiceover"))
    print("scenes:", result["scene_count"])
    for s in result["scenes"]:
        print(f"   #{s['index']} cap={s['caption']!r}  src={Path(s['source_path']).name}")


if __name__ == "__main__":
    main()
