"""Seed a generated trailer for a listing using the free stub backend.

Lets us validate the Listings/Reels API + UI without the Cosmos GPU tunnel.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, listings
from app.ffmpeg_utils import convert_image
from app.generation.stub import StubClipGenerator
from app.pipeline import generate_video

LISTING_ID = sys.argv[1] if len(sys.argv) > 1 else "la-house-1"


def main() -> None:
    listing = listings.get_listing(LISTING_ID)
    if listing is None:
        raise SystemExit(f"no such listing: {LISTING_ID}")

    job_dir = config.UPLOAD_DIR / f"listing_{LISTING_ID}"
    job_dir.mkdir(parents=True, exist_ok=True)
    media = []
    for i, src in enumerate(listing.photos[:14]):
        media.append(convert_image(str(src), str(job_dir / f"photo_{i:02d}.jpg")))

    result = generate_video(
        job_dir=job_dir,
        media_paths=media,
        instructions=listings.facts_for(listing),
        address=listing.name,
        lease="",
        generator=StubClipGenerator(),
        on_progress=lambda c, t, l: print(f"  [{c}/{t}] {l}"),
        mode="trailer",
    )
    published = listings.video_path(LISTING_ID)
    shutil.copyfile(Path(result["video_path"]), published)
    ec = result.get("end_card") or {}
    listings.save_meta(
        LISTING_ID,
        {
            "name": listing.name,
            "title": result.get("title") or listing.name,
            "location": ec.get("location") or "",
            "highlight": ec.get("highlight") or "",
            "voiceover": result.get("voiceover") or "",
            "scene_count": result.get("scene_count", 0),
        },
    )
    print("seeded:", published)


if __name__ == "__main__":
    main()
