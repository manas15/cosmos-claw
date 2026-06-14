"""Cosmos Claw web app: upload venue photos + instructions -> social videos.

Run locally:  uvicorn app.main:app --reload  (or `python -m app` )
Then open:    http://127.0.0.1:8000
"""

from __future__ import annotations

import shutil
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, listings
from .ffmpeg_utils import convert_image, extract_poster, ffmpeg_available, probe_duration
from .generation.factory import get_generator
from .pipeline import generate_video

app = FastAPI(title="Cosmos Claw", version="0.2.0")

STATIC_DIR = Path(__file__).parent / "static"
config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# In-memory job store for live progress (single-process dev server).
# job_id -> {status, current, total, label, video_url, backend, scene_count, error}
JOBS: dict[str, dict] = {}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


def _sponsors(gen, ready: bool) -> list[dict]:
    """Hackathon sponsor / partner badges with live enable status."""
    is_cosmos = gen.name.lower().startswith("cosmos") or "cosmos" in gen.name.lower()
    remote = bool(config.COSMOS_BASE_URL)
    openai_on = bool(config.OPENAI_API_KEY)
    return [
        {
            "id": "nvidia",
            "name": "NVIDIA Cosmos 3",
            "role": "Video model",
            "enabled": is_cosmos and ready,
            "detail": "generating" if (is_cosmos and ready) else "standby (local stub)",
        },
        {
            "id": "nebius",
            "name": "Nebius AI Cloud",
            "role": "GPU compute",
            "enabled": is_cosmos and remote,
            "detail": "endpoint connected" if remote else "no remote endpoint",
        },
        {
            "id": "openai",
            "name": "OpenAI",
            "role": "Director + voiceover",
            "enabled": openai_on,
            "detail": f"{config.OPENAI_MODEL} + TTS" if openai_on else "no API key",
        },
        {
            "id": "tavily",
            "name": "Tavily",
            "role": "Search partner",
            "enabled": bool(config.TAVILY_API_KEY),
            "detail": "connected" if config.TAVILY_API_KEY else "not connected",
        },
        {
            "id": "osm",
            "name": "OpenStreetMap",
            "role": "Maps & geocoding",
            "enabled": True,
            "detail": "active",
        },
    ]


@app.get("/api/health")
def health() -> JSONResponse:
    gen = get_generator()
    ready, reason = gen.available()
    return JSONResponse(
        {
            "backend": gen.name,
            "backend_ready": ready,
            "backend_reason": reason,
            "ffmpeg": ffmpeg_available(),
            "sponsors": _sponsors(gen, ready),
        }
    )


def _validate_ext(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    allowed = config.ALLOWED_IMAGE_EXTS | config.ALLOWED_VIDEO_EXTS | config.ALLOWED_DOC_EXTS
    if ext not in allowed:
        raise HTTPException(400, f"Unsupported file type: {ext or '(none)'}")
    return ext


def _run_job(
    job_id: str,
    job_dir: Path,
    media_paths: list[str],
    instructions: str,
    address: str,
    lease: str,
    mode: str,
) -> None:
    """Run the (blocking) pipeline on a worker thread, updating JOBS as it goes."""

    def on_progress(done: int, total: int, label: str) -> None:
        job = JOBS.get(job_id)
        if job is not None:
            job.update(current=done, total=total, label=label)

    try:
        generator = get_generator()
        result = generate_video(
            job_dir=job_dir,
            media_paths=media_paths,
            instructions=instructions,
            address=address,
            lease=lease,
            generator=generator,
            on_progress=on_progress,
            mode=mode,
        )
        published = config.OUTPUT_DIR / f"{job_id}.mp4"
        shutil.copyfile(Path(result["video_path"]), published)
        JOBS[job_id].update(
            status="done",
            video_url=f"/outputs/{published.name}",
            backend=result["backend"],
            scene_count=result["scene_count"],
            scenes=result["scenes"],
        )
    except Exception as e:  # surface ffmpeg/backend/director errors to the UI
        JOBS[job_id].update(status="error", error=f"Generation failed: {e}")


@app.post("/api/generate")
async def api_generate(
    files: list[UploadFile],
    instructions: str = Form(""),
    address: str = Form(""),
    lease: str = Form(""),
    mode: str = Form(config.DEFAULT_MODE),
) -> JSONResponse:
    if not files:
        raise HTTPException(400, "Upload at least one photo or video.")
    if len(files) > config.MAX_FILES:
        raise HTTPException(400, f"Too many files (max {config.MAX_FILES}).")

    job_id = uuid.uuid4().hex[:12]
    job_dir = config.UPLOAD_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    media_paths: list[str] = []
    pdf_facts: list[str] = []
    for i, f in enumerate(files):
        ext = _validate_ext(f.filename or f"file{i}")
        dest = job_dir / f"input_{i:02d}{ext}"
        with dest.open("wb") as out:
            shutil.copyfileobj(f.file, out)

        if ext in config.ALLOWED_DOC_EXTS:
            # Pull the listing facts out of the PDF and fold them into the brief.
            text = listings.extract_facts(str(dest))
            if text:
                pdf_facts.append(text)
            continue

        # AVIF/HEIC aren't reliably readable by Pillow/ffmpeg or the Cosmos
        # server — normalize them to baseline JPEG up front.
        if ext in config.NORMALIZE_IMAGE_EXTS:
            jpg = job_dir / f"input_{i:02d}.jpg"
            dest = Path(convert_image(str(dest), str(jpg)))
        media_paths.append(str(dest))

    if not media_paths:
        raise HTTPException(400, "Add at least one photo or video (a PDF alone isn't enough).")

    # Combine typed instructions with any extracted PDF facts.
    if pdf_facts:
        facts_block = "\n\n".join(pdf_facts)
        instructions = (
            f"{instructions.strip()}\n\nListing facts from PDF:\n{facts_block}".strip()
        )

    JOBS[job_id] = {
        "status": "running",
        "current": 0,
        "total": 0,
        "label": "Starting…",
        "video_url": None,
        "error": None,
    }

    # Generation is long and blocking (GPT-4o + several Cosmos calls + ffmpeg),
    # so run it off the event loop and let the client poll /api/job/{id}.
    threading.Thread(
        target=_run_job,
        args=(job_id, job_dir, media_paths, instructions, address, lease, mode),
        daemon=True,
    ).start()

    return JSONResponse({"job_id": job_id})


@app.get("/api/job/{job_id}")
def api_job(job_id: str) -> JSONResponse:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "Unknown job id.")
    return JSONResponse({"job_id": job_id, **job})


# --- Listings (Airbnb folders: photos + PDF) ---------------------------

MAX_LISTING_PHOTOS = 14  # cap vision/generation cost; director picks the best


def _version_url(path: Path, stamp: float) -> str:
    return f"/outputs/{path.name}?t={int(stamp)}"


def _poster_url(listing: listings.Listing) -> str | None:
    """A nice cover image: a frame from the latest trailer, else photo 0."""
    lv = listings.latest_version(listing.id)
    if lv and lv["poster"] is not None:
        return _version_url(lv["poster"], lv["poster"].stat().st_mtime)
    if listing.photos:
        return f"/api/listings/{listing.id}/photo/0"
    return None


def _version_payload(version: dict) -> dict:
    m = version["meta"]
    poster = version["poster"]
    return {
        "vid": version["vid"],
        "video_url": _version_url(version["video"], version["created_at"]),
        "poster": _version_url(poster, poster.stat().st_mtime) if poster else None,
        # epoch ms so the browser can format it in PST/PDT.
        "created_at": int(version["created_at"] * 1000),
        "title": m.get("title") or "",
        "location": m.get("location") or "",
        "price": m.get("price") or "",
        "scene_count": m.get("scene_count", 0),
        "info_card_count": m.get("info_card_count", 0),
    }


def _listing_payload(listing: listings.Listing) -> dict:
    facts = listings.facts_for(listing)
    lv = listings.latest_version(listing.id)
    meta = lv["meta"] if lv else {}
    return {
        "id": listing.id,
        "name": listing.name,
        "photo_count": len(listing.photos),
        "photos": [f"/api/listings/{listing.id}/photo/{i}" for i in range(len(listing.photos))],
        "facts_preview": (facts[:280] + "…") if len(facts) > 280 else facts,
        "has_pdf": listing.pdf is not None,
        "has_video": lv is not None,
        "video_count": len(listings.list_versions(listing.id)),
        "video_url": _version_url(lv["video"], lv["created_at"]) if lv else None,
        "poster": _poster_url(listing),
        "title": meta.get("title") or listing.name,
        "location": meta.get("location") or "",
        "price": meta.get("price") or "",
    }


@app.get("/api/listings")
def api_listings() -> JSONResponse:
    items = [_listing_payload(l) for l in listings.get_listings().values()]
    return JSONResponse({"listings": items, "listings_dir": str(config.LISTINGS_DIR)})


@app.get("/api/listings/{listing_id}")
def api_listing_detail(listing_id: str) -> JSONResponse:
    """Full listing: the source files (photos + PDF) and every generated video."""
    listing = listings.get_listing(listing_id)
    if listing is None:
        raise HTTPException(404, "Unknown listing.")
    facts = listings.facts_for(listing)
    versions = [_version_payload(v) for v in listings.list_versions(listing.id)]
    return JSONResponse(
        {
            "id": listing.id,
            "name": listing.name,
            "photo_count": len(listing.photos),
            "photos": [f"/api/listings/{listing.id}/photo/{i}" for i in range(len(listing.photos))],
            "facts": facts,
            "has_pdf": listing.pdf is not None,
            "pdf_name": listing.pdf.name if listing.pdf else None,
            "available": listings.available_includes(facts),
            "details": listings.extract_details(listing),
            "versions": versions,
        }
    )


@app.get("/api/listings/{listing_id}/photo/{idx}")
def api_listing_photo(listing_id: str, idx: int) -> FileResponse:
    listing = listings.get_listing(listing_id)
    if listing is None:
        raise HTTPException(404, "Unknown listing.")
    path = listings.thumb(listing, idx)
    if path is None:
        raise HTTPException(404, "Photo not found.")
    return FileResponse(path, media_type="image/jpeg")


def _run_listing_job(
    job_id: str,
    listing_id: str,
    extra_instructions: str = "",
    price: str = "",
    include: set[str] | None = None,
) -> None:
    """Generate a vertical trailer for a whole listing folder."""

    def on_progress(done: int, total: int, label: str) -> None:
        job = JOBS.get(job_id)
        if job is not None:
            job.update(current=done, total=total, label=label)

    print(f"[listing] generate {listing_id} include={sorted(include) if include else 'ALL'} price={price!r}")
    try:
        listing = listings.get_listing(listing_id)
        if listing is None:
            raise RuntimeError("Listing no longer exists.")

        job_dir = config.UPLOAD_DIR / f"listing_{listing_id}"
        job_dir.mkdir(parents=True, exist_ok=True)

        # Normalize the chosen photos to JPEG so Pillow/ffmpeg/Cosmos can read them.
        media_paths: list[str] = []
        for i, src in enumerate(listing.photos[:MAX_LISTING_PHOTOS]):
            dest = job_dir / f"photo_{i:02d}.jpg"
            media_paths.append(convert_image(str(src), str(dest)))

        # PDF facts ground the trailer; the user's director notes go on top.
        instructions = listings.facts_for(listing)
        if extra_instructions.strip():
            instructions = f"{extra_instructions.strip()}\n\n{instructions}".strip()

        result = generate_video(
            job_dir=job_dir,
            media_paths=media_paths,
            instructions=instructions,
            address=listing.name,
            lease=price,
            generator=get_generator(),
            on_progress=on_progress,
            mode="trailer",
            include=include,
        )

        # Each generation is its own version so the listing keeps a full history.
        vid = listings.new_version_id()
        created_at = time.time()
        vpath, ppath, _ = listings.version_paths(listing_id, vid)
        shutil.copyfile(Path(result["video_path"]), vpath)

        # A clean cover frame from ~20% in (past the opening fade, before the
        # end card) so the video doesn't show a black poster.
        try:
            dur = probe_duration(str(vpath)) or 10.0
            extract_poster(str(vpath), str(ppath), at_seconds=max(1.0, dur * 0.2))
        except Exception as exc:  # noqa: BLE001
            print(f"[listing] poster extraction failed: {exc}")

        end_card = result.get("end_card") or {}
        listings.save_version_meta(
            listing_id,
            vid,
            {
                "name": listing.name,
                "title": result.get("title") or listing.name,
                "location": result.get("location") or end_card.get("location") or "",
                "highlight": end_card.get("highlight") or "",
                "price": result.get("price") or "",
                "voiceover": result.get("voiceover") or "",
                "scene_count": result.get("scene_count", 0),
                "info_card_count": result.get("info_card_count", 0),
                "created_at": created_at,
            },
        )
        JOBS[job_id].update(
            status="done",
            video_url=f"/outputs/{vpath.name}",
            backend=result["backend"],
            scene_count=result["scene_count"],
            listing_id=listing_id,
            vid=vid,
        )
    except Exception as e:  # surface errors to the UI
        JOBS[job_id].update(status="error", error=f"Generation failed: {e}")


@app.post("/api/listings/{listing_id}/generate")
def api_listing_generate(
    listing_id: str,
    instructions: str = Form(""),
    price: str = Form(""),
    include: str = Form(""),
) -> JSONResponse:
    listing = listings.get_listing(listing_id)
    if listing is None:
        raise HTTPException(404, "Unknown listing.")

    # Comma-separated include keys; empty -> include everything (default).
    inc = {p.strip() for p in include.split(",") if p.strip()} or None

    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {
        "status": "running",
        "current": 0,
        "total": 0,
        "label": "Starting…",
        "video_url": None,
        "error": None,
        "listing_id": listing_id,
    }
    threading.Thread(
        target=_run_listing_job,
        args=(job_id, listing_id, instructions, price, inc),
        daemon=True,
    ).start()
    return JSONResponse({"job_id": job_id})


@app.get("/api/reels")
def api_reels() -> JSONResponse:
    """Generated trailers, newest first, for the vertical reel feed."""
    reels = []
    for listing in listings.get_listings().values():
        lv = listings.latest_version(listing.id)
        if lv is None:
            continue
        meta = lv["meta"]
        reels.append(
            {
                "id": listing.id,
                "name": listing.name,
                "title": meta.get("title") or listing.name,
                "location": meta.get("location") or "",
                "highlight": meta.get("highlight") or "",
                "price": meta.get("price") or "",
                "video_url": _version_url(lv["video"], lv["created_at"]),
                "poster": _poster_url(listing),
                "mtime": lv["created_at"],
            }
        )
    reels.sort(key=lambda r: r["mtime"], reverse=True)
    return JSONResponse({"reels": reels})


# Static assets + generated videos.
app.mount("/outputs", StaticFiles(directory=str(config.OUTPUT_DIR)), name="outputs")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
