"""Listing catalog: each folder under ``LISTINGS_DIR`` is one Airbnb listing.

A listing folder holds the property photos (jpg/png/webp/avif/heic) plus a PDF
export of the listing page. We scan that folder, extract the PDF text as the
"facts" that ground the trailer, serve web-friendly JPEG thumbnails (AVIF/HEIC
get normalized), and remember which listings already have a generated trailer.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import config
from .ffmpeg_utils import convert_image

_IMAGE_EXTS = config.ALLOWED_IMAGE_EXTS
_THUMB_PX = 900


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "listing"


@dataclass
class Listing:
    id: str
    name: str
    dir: Path
    photos: list[Path]
    pdf: Path | None


def _scan() -> dict[str, Listing]:
    listings: dict[str, Listing] = {}
    base = config.LISTINGS_DIR
    if not base.exists():
        return listings
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        photos = sorted(
            p for p in d.iterdir() if p.suffix.lower() in _IMAGE_EXTS and p.is_file()
        )
        if not photos:
            continue
        pdfs = sorted(p for p in d.iterdir() if p.suffix.lower() == ".pdf")
        lid = slugify(d.name)
        listings[lid] = Listing(
            id=lid, name=d.name, dir=d, photos=photos, pdf=pdfs[0] if pdfs else None
        )
    return listings


def get_listings() -> dict[str, Listing]:
    """Fresh scan each call so dropping in a new folder shows up on refresh."""
    return _scan()


def get_listing(listing_id: str) -> Listing | None:
    return get_listings().get(listing_id)


@lru_cache(maxsize=64)
def extract_facts(pdf_path: str) -> str:
    """Pull readable text out of the listing PDF (best-effort), trimmed."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(pdf_path)
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:  # noqa: BLE001
        print(f"[listings] PDF parse failed for {pdf_path}: {exc}")
        return ""

    # Collapse whitespace and drop obvious chrome/navigation noise.
    lines: list[str] = []
    for ln in text.splitlines():
        ln = " ".join(ln.split())
        if not ln:
            continue
        low = ln.lower()
        if low in {"airbnb", "share", "save", "show all photos", "report this listing"}:
            continue
        lines.append(ln)
    joined = "\n".join(lines)
    return joined[:4000]


def facts_for(listing: Listing) -> str:
    if not listing.pdf:
        return ""
    return extract_facts(str(listing.pdf))


# Keyword signals for figuring out which trailer ingredients the PDF supports.
_TRANSIT_KW = (
    "metro", "subway", "train", "station", "bus", "transit", "walk to",
    "min walk", "minute walk", "minutes to", "blocks from", "bike", "freeway",
    "highway", "downtown", "airport", "commute",
)
_AMENITY_KW = (
    "wifi", "wi-fi", "air conditioning", "central air", "heating", "kitchen",
    "parking", "pool", "hot tub", "washer", "dryer", "workspace", "patio",
    "balcony", "garden", "gym", "bbq", "fireplace", "coffee", "dishwasher",
    "netflix", "tv", "self check-in", "ac ",
)
_HOST_KW = (
    "superhost", "hosted by", "host", "review", "rating", "years hosting",
    "guest favorite", "stars", "highly ranked",
)
_LOCATION_KW = (
    "neighborhood", "located", "near", "minutes from", "mins from",
    "close to", "in the heart of", "walk to",
)


def detect_price(facts: str) -> bool:
    """True if the facts mention a per-night price."""
    f = facts.lower()
    has_amount = re.search(r"\$\s?\d", f) is not None
    return has_amount and any(k in f for k in ("night", "/night", "per night", "nightly"))


def available_includes(facts: str) -> list[str]:
    """Which 'include' options the PDF actually backs (photos are always on)."""
    f = (facts or "").lower()
    avail = ["photos"]
    has_city_state = re.search(r"\bin\s+[a-z .'\-]+,\s*[a-z]", f) is not None
    if has_city_state or any(k in f for k in _LOCATION_KW):
        avail.append("map")
    if any(k in f for k in _TRANSIT_KW):
        avail.append("transit")
    if any(k in f for k in _AMENITY_KW):
        avail.append("amenities")
    if any(k in f for k in _HOST_KW):
        avail.append("host")
    if detect_price(f):
        avail.append("price")
    return avail


# --- structured listing details (a pre-filled, editable form) -----------

_DETAIL_STR_KEYS = (
    "title", "location", "price", "guests", "bedrooms", "beds", "baths",
    "rating", "reviews", "host", "summary",
)


def details_cache_path(listing_id: str) -> Path:
    return config.OUTPUT_DIR / f"listing_{listing_id}_facts.json"


def _empty_details(listing: Listing) -> dict:
    d = {k: "" for k in _DETAIL_STR_KEYS}
    d.update(title=listing.name, superhost=False, amenities=[], nearby=[])
    return d


def _norm_details(data: dict, base: dict) -> dict:
    out = dict(base)
    for k in _DETAIL_STR_KEYS:
        v = data.get(k)
        if isinstance(v, (str, int, float)) and str(v).strip():
            out[k] = str(v).strip()
    out["superhost"] = bool(data.get("superhost"))
    for key in ("amenities", "nearby"):
        items = data.get(key) or []
        if isinstance(items, list):
            out[key] = [str(x).strip() for x in items if str(x).strip()][:8]
    return out


def _gpt_details(facts: str) -> dict | None:
    if not config.OPENAI_API_KEY:
        return None
    try:
        from openai import OpenAI

        client = OpenAI(api_key=config.OPENAI_API_KEY)
        system = (
            "You extract structured fields from a short-term-rental listing's text. "
            "Return STRICT JSON with keys: title, location, price, guests, bedrooms, "
            "beds, baths, rating, reviews, superhost (boolean), host, summary, "
            "amenities (array of short strings), nearby (array of short strings such "
            "as '15 min walk to Runyon Canyon' or '5 min to the metro'). Use ONLY the "
            "provided text; never invent. Unknown string -> \"\", unknown list -> []. "
            "price should be per-night like '$239 / night' if present. 'summary' is one "
            "warm sentence describing the place."
        )
        resp = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=700,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": facts[:4000]},
            ],
        )
        return json.loads(resp.choices[0].message.content or "{}")
    except Exception as exc:  # noqa: BLE001
        print(f"[listings] detail extraction failed: {exc}")
        return None


def _heuristic_details(facts: str) -> dict:
    f = facts
    low = f.lower()
    out: dict = {"superhost": "superhost" in low, "amenities": [], "nearby": []}
    m = re.search(r"\b([1-5]\.\d{1,2})\b", f)
    if m:
        out["rating"] = m.group(1)
    m = re.search(r"(\d{1,4})\s+reviews?", low)
    if m:
        out["reviews"] = m.group(1)
    m = re.search(r"\$\s?\d[\d,]*", f)
    if m and detect_price(f):
        out["price"] = m.group(0).replace(" ", "") + " / night"
    for key, pat in (("guests", r"(\d+)\s+guests?"), ("bedrooms", r"(\d+)\s+bedrooms?"),
                     ("beds", r"(\d+)\s+beds?"), ("baths", r"(\d+(?:\.\d)?)\s+baths?")):
        mm = re.search(pat, low)
        if mm:
            out[key] = mm.group(0)
    return out


def extract_details(listing: Listing) -> dict:
    """A structured, editable view of the listing (cached to disk per PDF)."""
    base = _empty_details(listing)
    facts = facts_for(listing)
    if not facts:
        return base

    cache = details_cache_path(listing.id)
    if listing.pdf and cache.exists() and cache.stat().st_mtime >= listing.pdf.stat().st_mtime:
        try:
            return {**base, **json.loads(cache.read_text(encoding="utf-8"))}
        except Exception:  # noqa: BLE001
            pass

    merged = _norm_details(_gpt_details(facts) or _heuristic_details(facts), base)
    if not merged.get("title"):
        merged["title"] = listing.name
    try:
        cache.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return merged


def thumb(listing: Listing, idx: int) -> Path | None:
    """Return a cached web-servable JPEG for photo ``idx`` (convert if needed)."""
    if idx < 0 or idx >= len(listing.photos):
        return None
    src = listing.photos[idx]
    config.THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    out = config.THUMBS_DIR / f"{listing.id}_{idx:02d}.jpg"
    if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
        return out

    # Convert + downscale via Pillow; fall back to sips for AVIF/HEIC.
    try:
        from PIL import Image

        with Image.open(src) as im:
            im = im.convert("RGB")
            im.thumbnail((_THUMB_PX, _THUMB_PX))
            im.save(out, format="JPEG", quality=85)
        return out
    except Exception:
        result = convert_image(str(src), str(out))
        return Path(result) if Path(result).exists() else None


# --- generated-trailer bookkeeping --------------------------------------

def video_path(listing_id: str) -> Path:
    return config.OUTPUT_DIR / f"listing_{listing_id}.mp4"


def meta_path(listing_id: str) -> Path:
    return config.OUTPUT_DIR / f"listing_{listing_id}.json"


def poster_path(listing_id: str) -> Path:
    return config.OUTPUT_DIR / f"listing_{listing_id}.jpg"


def has_poster(listing_id: str) -> bool:
    p = poster_path(listing_id)
    return p.exists() and p.stat().st_size > 0


def save_meta(listing_id: str, meta: dict) -> None:
    meta_path(listing_id).write_text(json.dumps(meta, indent=2), encoding="utf-8")


def load_meta(listing_id: str) -> dict:
    p = meta_path(listing_id)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def has_video(listing_id: str) -> bool:
    return bool(list_versions(listing_id))


# --- versioned trailers (history of every generation) -------------------
# Each generation produces its own files so we keep the full history:
#   listing_{lid}_{vid}.mp4 / .jpg (poster) / .json (meta with created_at)
# A pre-versioning single file (listing_{lid}.mp4) still shows as one entry.

def new_version_id() -> str:
    return uuid.uuid4().hex[:10]


def version_paths(listing_id: str, vid: str) -> tuple[Path, Path, Path]:
    base = config.OUTPUT_DIR / f"listing_{listing_id}_{vid}"
    return base.with_suffix(".mp4"), base.with_suffix(".jpg"), base.with_suffix(".json")


def takes_dir(listing_id: str, vid: str) -> Path:
    """Per-version dir holding best-of-N takes + the re-stitch manifest.

    Lives under OUTPUT_DIR so the take previews are web-served at /outputs/...
    """
    return config.OUTPUT_DIR / f"listing_{listing_id}_{vid}_takes"


def takes_manifest_path(listing_id: str, vid: str) -> Path:
    return takes_dir(listing_id, vid) / "manifest.json"


def save_version_meta(listing_id: str, vid: str, meta: dict) -> None:
    _, _, mpath = version_paths(listing_id, vid)
    tmp = mpath.with_suffix(mpath.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    os.replace(tmp, mpath)


def prune_versions(listing_id: str, keep: int | None = None) -> int:
    """Bound disk use over a months-long run: keep the ``keep`` most recent
    versions, deleting older ones — but NEVER delete a cut the human posted.

    Returns the number of versions pruned. ``keep`` defaults to
    ``config.VERSION_RETENTION``; 0 disables pruning.
    """
    keep = config.VERSION_RETENTION if keep is None else keep
    if keep <= 0:
        return 0
    versions = list_versions(listing_id)  # newest first
    if len(versions) <= keep:
        return 0

    pruned = 0
    for v in versions[keep:]:
        if v.get("vid") == "v1":  # legacy single-file trailer — leave it
            continue
        if str((v.get("meta") or {}).get("status", "")).lower() == "posted":
            continue  # keep anything we actually published
        vid = v["vid"]
        mp4, jpg, meta = version_paths(listing_id, vid)
        for p in (mp4, jpg, meta):
            try:
                p.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
        td = takes_dir(listing_id, vid)
        if td.exists():
            try:
                shutil.rmtree(td, ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass
        pruned += 1
    return pruned


def _read_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def list_versions(listing_id: str) -> list[dict]:
    """All generated trailers for a listing, newest first."""
    prefix = f"listing_{listing_id}_"
    out: list[dict] = []
    for vp in config.OUTPUT_DIR.glob(f"{prefix}*.mp4"):
        if vp.stat().st_size == 0:
            continue
        vid = vp.name[len(prefix):-4]  # strip the ".mp4"
        if not vid:
            continue
        meta = _read_json(vp.with_suffix(".json"))
        poster = vp.with_suffix(".jpg")
        out.append(
            {
                "vid": vid,
                "video": vp,
                "poster": poster if poster.exists() else None,
                "meta": meta,
                "created_at": float(meta.get("created_at") or vp.stat().st_mtime),
            }
        )

    # Legacy single-file trailer (generated before versioning).
    legacy = video_path(listing_id)
    if legacy.exists() and legacy.stat().st_size > 0:
        meta = load_meta(listing_id)
        poster = poster_path(listing_id)
        out.append(
            {
                "vid": "v1",
                "video": legacy,
                "poster": poster if poster.exists() else None,
                "meta": meta,
                "created_at": float(meta.get("created_at") or legacy.stat().st_mtime),
            }
        )

    out.sort(key=lambda v: v["created_at"], reverse=True)
    return out


def latest_version(listing_id: str) -> dict | None:
    versions = list_versions(listing_id)
    return versions[0] if versions else None
