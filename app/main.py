"""Cosmos Claw web app: upload venue photos + instructions -> social videos.

Run locally:  uvicorn app.main:app --reload  (or `python -m app` )
Then open:    http://127.0.0.1:8000
"""

from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import brand, config, infocards, listings
from .ffmpeg_utils import convert_image, extract_poster, ffmpeg_available, probe_duration
from .generation.factory import get_generator
from .pipeline import generate_video, restitch_from_manifest

# Generations mutate the output canvas (TRAILER_WIDTH/HEIGHT) per chosen social
# format, so serialize them to keep one job's dimensions from racing another's.
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
        "format": m.get("format") or "",
        "format_label": m.get("format_label") or "",
        "ratio": m.get("ratio") or "",
        "voice": m.get("voice") or "",
        "music": m.get("music") or "",
        "handle": m.get("handle") or "",
        "caption": m.get("caption") or "",
        "hashtags": m.get("hashtags") or [],
        "best_of_n": m.get("best_of_n", 1),
        "has_takes": bool(m.get("has_takes")),
        "takes": m.get("takes") or [],
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


def _apply_format(fmt: str) -> dict:
    """Resolve a social format to its preset and point the render canvas at it.

    Mutates the module-level dimensions both modules read (config reads at call
    time; infocards captured W/H at import, so refresh those too).
    """
    preset = config.FORMAT_PRESETS.get(fmt) or config.FORMAT_PRESETS[config.DEFAULT_FORMAT]
    config.TRAILER_WIDTH = preset["w"]
    config.TRAILER_HEIGHT = preset["h"]
    infocards.W, infocards.H = preset["w"], preset["h"]
    return preset


def _persist_takes(listing_id: str, vid: str, manifest: dict) -> dict:
    """Copy best-of-N takes + audio into the version's takes dir and save the
    re-stitch manifest. Returns the compact per-beat takes for the version meta."""
    tdir = listings.takes_dir(listing_id, vid)
    tdir.mkdir(parents=True, exist_ok=True)
    compact: list[dict] = []
    for si, seg in enumerate(manifest.get("segments", [])):
        if seg.get("type") == "room":
            beat = int(seg.get("beat", si))
            kept: list[dict] = []
            ctakes: list[dict] = []
            for t in seg.get("takes", []):
                k = int(t.get("index", 0))
                dest = tdir / f"beat{beat:02d}_t{k}.mp4"
                try:
                    shutil.copyfile(t["clip"], dest)
                except Exception:  # noqa: BLE001
                    continue
                poster = tdir / f"beat{beat:02d}_t{k}.jpg"
                try:
                    dur = probe_duration(str(dest)) or 4.0
                    extract_poster(str(dest), str(poster), at_seconds=max(0.3, dur * 0.4))
                except Exception:  # noqa: BLE001
                    poster = None
                t["clip"] = str(dest)  # rewrite manifest to the persisted path
                kept.append(t)
                ctakes.append(
                    {
                        "index": k,
                        "url": f"/outputs/{tdir.name}/{dest.name}",
                        "poster": (
                            f"/outputs/{tdir.name}/{poster.name}"
                            if poster and poster.exists() else None
                        ),
                        "score": t.get("score"),
                        "motion": t.get("motion"),
                        "smoothness": t.get("smoothness"),
                        "chosen": bool(t.get("chosen")),
                    }
                )
            seg["takes"] = kept
            compact.append(
                {
                    "beat": beat,
                    "caption": seg.get("caption", ""),
                    "shot": seg.get("shot", ""),
                    "chosen": int(seg.get("chosen", 0)),
                    "takes": ctakes,
                }
            )
        elif seg.get("clip"):
            dest = tdir / f"seg{si:02d}.mp4"
            try:
                shutil.copyfile(seg["clip"], dest)
                seg["clip"] = str(dest)
            except Exception:  # noqa: BLE001
                pass

    audio = manifest.get("audio") or {}
    for key, name in (("music", "music.wav"), ("voiceover", "vo.mp3")):
        src = audio.get(key)
        if src and Path(src).exists():
            dest = tdir / name
            try:
                shutil.copyfile(src, dest)
                audio[key] = str(dest)
            except Exception:  # noqa: BLE001
                pass
    manifest["audio"] = audio
    listings.takes_manifest_path(listing_id, vid).write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return {"beats": compact}


def _run_listing_job(
    job_id: str,
    listing_id: str,
    extra_instructions: str = "",
    price: str = "",
    include: set[str] | None = None,
    fmt: str = "",
    walkthrough: bool = False,
) -> None:
    """Generate a social trailer for a whole listing folder."""

    def on_progress(done: int, total: int, label: str) -> None:
        job = JOBS.get(job_id)
        if job is not None:
            job.update(current=done, total=total, label=label)

    fmt = (fmt or config.DEFAULT_FORMAT).lower()
    print(f"[listing] generate {listing_id} fmt={fmt} include={sorted(include) if include else 'ALL'} price={price!r}")
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

        # The brand dossier is the consistency anchor: every generation grounds on
        # the same (real + fabricated) facts and follows the marketing brief.
        dossier = brand.load_or_seed(listing)
        grounding = brand.grounding_text(dossier)
        brief = dossier.get("brief") or None
        if not price:
            price = (dossier.get("facts") or {}).get("price") or ""
        if walkthrough:
            brand.log_activity(
                listing_id, "🚶", f"Filming a first-person walkthrough ({fmt})", "generate"
            )
        else:
            brand.log_activity(listing_id, "🎬", f"Filming a {fmt} cut from the brief", "generate")

        # Hold the lock for the whole render: the canvas dimensions are global.
        with GEN_LOCK:
            preset = _apply_format(fmt)
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
                brief=brief,
                grounding=grounding,
                walkthrough=walkthrough,
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

        # Best-of-N: persist every take + the re-stitch manifest so the Studio
        # take picker can swap a beat's take without re-running Cosmos.
        takes_beats: list[dict] = []
        if result.get("takes_manifest"):
            try:
                takes_beats = _persist_takes(listing_id, vid, result["takes_manifest"]).get("beats", [])
            except Exception as exc:  # noqa: BLE001
                print(f"[listing] takes persist failed: {exc}")

        end_card = result.get("end_card") or {}
        # A copy-paste-ready social post for this cut (handle, caption, hashtags).
        post = brand.build_post(dossier, preset["label"])
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
                "format": fmt,
                "format_label": preset["label"],
                "ratio": preset["ratio"],
                "voice": (brief or {}).get("voice") or config.TTS_VOICE,
                "music": (brief or {}).get("music") or "warm",
                "handle": post["handle"],
                "caption": post["caption"],
                "hashtags": post["hashtags"],
                "best_of_n": result.get("best_of_n", 1),
                "has_takes": bool(takes_beats),
                "takes": takes_beats,
                "created_at": created_at,
            },
        )
        brand.log_activity(
            listing_id, "✅", f"Published a {preset['label']} cut ({preset['ratio']})", "publish"
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
    fmt: str = Form(""),
    walkthrough: str = Form(""),
) -> JSONResponse:
    listing = listings.get_listing(listing_id)
    if listing is None:
        raise HTTPException(404, "Unknown listing.")

    # Comma-separated include keys; empty -> include everything (default).
    inc = {p.strip() for p in include.split(",") if p.strip()} or None
    walk = walkthrough.strip().lower() in ("1", "true", "on", "yes")

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
        args=(job_id, listing_id, instructions, price, inc, fmt, walk),
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


def _run_agent_job(job_id: str, listing_id: str, fmt: str) -> None:
    """Background: run a full marketing-manager pass, streaming steps to the job."""
    from . import marketing_agent

    def on_step(icon: str, text: str) -> None:
        job = JOBS.get(job_id)
        if job is not None:
            job.update(label=f"{icon} {text}")

    try:
        listing = listings.get_listing(listing_id)
        if listing is None:
            raise RuntimeError("Listing no longer exists.")
        marketing_agent.run(listing, fmt=fmt, on_step=on_step)
        JOBS[job_id].update(status="done", label="✨ Brief ready", listing_id=listing_id)
    except Exception as e:  # surface to UI
        JOBS[job_id].update(status="error", error=f"Agent run failed: {e}")


@app.post("/api/listings/{listing_id}/agent/run")
def api_listing_agent_run(listing_id: str, fmt: str = Form("")) -> JSONResponse:
    listing = listings.get_listing(listing_id)
    if listing is None:
        raise HTTPException(404, "Unknown listing.")
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {
        "status": "running", "current": 0, "total": 0,
        "label": "Marketing manager starting…", "error": None, "listing_id": listing_id,
    }
    threading.Thread(
        target=_run_agent_job, args=(job_id, listing_id, fmt), daemon=True
    ).start()
    return JSONResponse({"job_id": job_id})


@app.post("/api/listings/{listing_id}/versions/{vid}/restitch")
def api_restitch(listing_id: str, vid: str, picks: str = Form("")) -> JSONResponse:
    """Re-stitch a best-of-N version using the chosen take per beat.

    ``picks`` is a JSON object mapping beat index -> take index. Re-concats +
    re-muxes from the persisted manifest (no Cosmos re-run) and overwrites the
    version's MP4 + poster.
    """
    listing = listings.get_listing(listing_id)
    if listing is None:
        raise HTTPException(404, "Unknown listing.")
    mpath = listings.takes_manifest_path(listing_id, vid)
    if not mpath.exists():
        raise HTTPException(404, "No takes saved for this version.")

    try:
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        raise HTTPException(500, "Corrupt takes manifest.")

    try:
        raw_picks = json.loads(picks or "{}")
        pick_map = {int(k): int(v) for k, v in raw_picks.items()}
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "picks must be a JSON object of beat->take ints.")

    vpath, ppath, meta_p = listings.version_paths(listing_id, vid)
    work = config.UPLOAD_DIR / f"listing_{listing_id}" / "restitch"
    with GEN_LOCK:
        restitch_from_manifest(manifest, pick_map, str(vpath), work)

    # Persist the new chosen takes back into the manifest + version meta.
    for seg in manifest.get("segments", []):
        if seg.get("type") == "room":
            b = int(seg.get("beat", -1))
            if b in pick_map:
                seg["chosen"] = pick_map[b]
                for t in seg.get("takes", []):
                    t["chosen"] = t.get("index") == pick_map[b]
    mpath.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    try:
        dur = probe_duration(str(vpath)) or 10.0
        extract_poster(str(vpath), str(ppath), at_seconds=max(1.0, dur * 0.2))
    except Exception as exc:  # noqa: BLE001
        print(f"[restitch] poster failed: {exc}")

    meta = listings._read_json(meta_p)
    for beat in meta.get("takes", []):
        b = int(beat.get("beat", -1))
        if b in pick_map:
            beat["chosen"] = pick_map[b]
            for t in beat.get("takes", []):
                t["chosen"] = t.get("index") == pick_map[b]
    listings.save_version_meta(listing_id, vid, meta)

    for v in listings.list_versions(listing_id):
        if v["vid"] == vid:
            return JSONResponse(_version_payload(v))
    raise HTTPException(404, "Version not found after re-stitch.")


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
