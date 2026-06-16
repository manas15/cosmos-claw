"""Cosmos Claw web app: upload venue photos + instructions -> social videos.

Run locally:  uvicorn app.main:app --reload  (or `python -m app` )
Then open:    http://127.0.0.1:8000
"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import brand, config, listings, videographer, vision
from .ffmpeg_utils import convert_image, ffmpeg_available
from .generation.factory import get_generator

# make_reel mutates a global frame count (config.COSMOS_NUM_FRAMES) per cut, so
# serialize web generations to keep one job from racing another's settings.
GEN_LOCK = threading.Lock()

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


@app.get("/api/job/{job_id}")
def api_job(job_id: str) -> JSONResponse:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "Unknown job id.")
    return JSONResponse({"job_id": job_id, **job})


# --- Projects (folders: photos + optional PDF) -------------------------

MAX_LISTING_PHOTOS = 14  # cap vision/generation cost; the brief picks the best


def _version_url(path: Path, stamp: float) -> str:
    return f"/outputs/{path.name}?t={int(stamp)}"


def _poster_url(listing: listings.Listing) -> str | None:
    """A nice cover image: a frame from the latest cut, else photo 0."""
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
        "format": m.get("format") or "",
        "format_label": m.get("format_label") or "",
        "ratio": m.get("ratio") or "",
        "voice": m.get("voice") or "",
        "music": m.get("music") or "",
        "handle": m.get("handle") or "",
        "caption": m.get("caption") or "",
        "hashtags": m.get("hashtags") or [],
        "voiceover": m.get("voiceover") or "",
        "source": m.get("source") or "",
        # Feedback lifecycle (the Checker).
        "status": m.get("status") or "pending_review",
        "slop_notes": m.get("slop_notes") or "",
        "posted_at": int(m["posted_at"] * 1000) if m.get("posted_at") else None,
        "review_due": int(m["review_due"] * 1000) if m.get("review_due") else None,
        "performance": (m.get("performance") or {}).get("metrics") or None,
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
            "brand": brand.load_or_seed(listing),
            "goals": _goals_progress(listing_id),
            "versions": versions,
        }
    )


def _goals_progress(listing_id: str) -> list[dict]:
    from . import goals

    try:
        return goals.progress(listing_id)
    except Exception:  # noqa: BLE001
        return []


@app.get("/api/listings/{listing_id}/goals")
def api_listing_goals(listing_id: str) -> JSONResponse:
    if listings.get_listing(listing_id) is None:
        raise HTTPException(404, "Unknown listing.")
    return JSONResponse({"goals": _goals_progress(listing_id)})


@app.get("/api/listings/{listing_id}/photo/{idx}")
def api_listing_photo(listing_id: str, idx: int) -> FileResponse:
    listing = listings.get_listing(listing_id)
    if listing is None:
        raise HTTPException(404, "Unknown listing.")
    path = listings.thumb(listing, idx)
    if path is None:
        raise HTTPException(404, "Photo not found.")
    return FileResponse(path, media_type="image/jpeg")


def _web_asset_index(listing: listings.Listing, dossier: dict, media_paths: list[str],
                     work: Path) -> list[dict]:
    """Label each chosen photo (reusing the dossier's cached labels when the
    count matches, so the UI doesn't pay for vision on every click)."""
    cached = dossier.get("asset_index") or []
    context = (dossier.get("brand") or {}).get("oneliner") or dossier.get("use_case") or ""
    index: list[dict] = []
    for i, p in enumerate(media_paths):
        if i < len(cached) and len(cached) == len(media_paths):
            c = cached[i]
            index.append({"index": i, "path": p, "label": c.get("label", ""),
                          "shot": c.get("shot", "walk forward"), "prompt": c.get("prompt", "")})
        else:
            info = vision.analyze(p, work, i, context=context)
            index.append({"index": i, "path": p, **info})
    return index


def _run_listing_job(
    job_id: str,
    listing_id: str,
    extra_instructions: str = "",
    price: str = "",
    fmt: str = "",
) -> None:
    """Generate one social reel for a whole project folder via the videographer."""

    def on_progress(done: int, total: int, label: str) -> None:
        job = JOBS.get(job_id)
        if job is not None:
            job.update(current=done, total=total, label=label)

    fmt = (fmt or config.DEFAULT_FORMAT).lower()
    print(f"[listing] generate {listing_id} fmt={fmt} price={price!r}")
    try:
        listing = listings.get_listing(listing_id)
        if listing is None:
            raise RuntimeError("Listing no longer exists.")

        job_dir = config.UPLOAD_DIR / f"listing_{listing_id}"
        job_dir.mkdir(parents=True, exist_ok=True)

        # Normalize photos to JPEG so Pillow/ffmpeg/the backend can read them.
        media_paths: list[str] = []
        for i, src in enumerate(listing.photos[:MAX_LISTING_PHOTOS]):
            dest = job_dir / f"photo_{i:02d}.jpg"
            media_paths.append(convert_image(str(src), str(dest)))
        if len(media_paths) < 2:
            raise RuntimeError("Need at least 2 photos to film a reel.")

        dossier = brand.load_or_seed(listing)
        if price and not (dossier.get("facts") or {}).get("price"):
            brand.add_assumption(listing_id, "price", price)
            dossier = brand.load(listing_id) or dossier
        brand_ = dossier.get("brand") or {}
        brief = dossier.get("brief") or {}

        asset_index = _web_asset_index(listing, dossier, media_paths, job_dir)
        idea = {
            "theme": (extra_instructions.strip()[:70] or f"{listing.name} — fresh cut"),
            "angle": extra_instructions.strip()[:160],
            "format": fmt,
            "music": brief.get("music") or brand_.get("music") or "uplifting",
            "voice": brief.get("voice") or brand_.get("voice") or config.TTS_VOICE,
            "photo_indices": [a["index"] for a in asset_index],
            "caption": "",  # build_post fills a grounded caption
            "voiceover": brief.get("voiceover") or "",
            "hashtags": [],
        }

        with GEN_LOCK:
            vid = videographer.make_reel(
                listing, dossier, idea, get_generator(), job_dir,
                asset_index=asset_index,
                on_progress=on_progress,
                source="web",
            )
        if not vid:
            raise RuntimeError("Reel generation produced no clips.")

        vpath, _, _ = listings.version_paths(listing_id, vid)
        JOBS[job_id].update(
            status="done",
            video_url=f"/outputs/{vpath.name}",
            backend=get_generator().name,
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
    fmt: str = Form(""),
) -> JSONResponse:
    listing = listings.get_listing(listing_id)
    if listing is None:
        raise HTTPException(404, "Unknown listing.")

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
        args=(job_id, listing_id, instructions, price, fmt),
        daemon=True,
    ).start()
    return JSONResponse({"job_id": job_id})


@app.get("/api/listings/{listing_id}/brand")
def api_listing_brand(listing_id: str) -> JSONResponse:
    """The marketing dossier (brand + assumptions + brief + activity) for a project."""
    listing = listings.get_listing(listing_id)
    if listing is None:
        raise HTTPException(404, "Unknown listing.")
    return JSONResponse(brand.load_or_seed(listing))


def _version_payload_for(listing_id: str, vid: str) -> JSONResponse:
    for v in listings.list_versions(listing_id):
        if v["vid"] == vid:
            return JSONResponse(_version_payload(v))
    raise HTTPException(404, "Version not found.")


@app.post("/api/listings/{listing_id}/versions/{vid}/decision")
def api_version_decision(
    listing_id: str, vid: str,
    decision: str = Form(...), notes: str = Form(""),
) -> JSONResponse:
    """The Checker: POST a cut live or DISCARD it as slop (with an optional why)."""
    from . import feedback

    if listings.get_listing(listing_id) is None:
        raise HTTPException(404, "Unknown listing.")
    try:
        feedback.record_decision(listing_id, vid, decision, slop_notes=notes)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _version_payload_for(listing_id, vid)


@app.post("/api/listings/{listing_id}/versions/{vid}/performance")
def api_version_performance(
    listing_id: str, vid: str,
    views: str = Form(""), likes: str = Form(""), comments: str = Form(""),
    shares: str = Form(""), followers: str = Form(""),
) -> JSONResponse:
    """Log how a posted cut performed; feeds lessons + goals."""
    from . import feedback

    if listings.get_listing(listing_id) is None:
        raise HTTPException(404, "Unknown listing.")
    metrics = {}
    for name, raw in (("views", views), ("likes", likes), ("comments", comments),
                      ("shares", shares), ("followers", followers)):
        if str(raw).strip():
            metrics[name] = raw
    if not metrics:
        raise HTTPException(400, "Provide at least one metric.")
    try:
        feedback.record_performance(listing_id, vid, metrics)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _version_payload_for(listing_id, vid)


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
