"""Informational cards interleaved with the room b-roll.

These tell the *story around* the property — where it is on a real map, what's
nearby (restaurants, parks, transit), the nightly price, and the rating. They're
rendered as full-frame vertical PNGs (matching the trailer canvas) and turned
into short clips by the pipeline.

A neighborhood map needs the internet (Nominatim geocoding + map tiles); every
renderer degrades gracefully (e.g. map -> plain neighborhood list) so a trailer
always builds.
"""

from __future__ import annotations

import time
from functools import lru_cache
from pathlib import Path

import requests
from PIL import Image, ImageDraw

from . import config
from .text_overlay import _font

W, H = config.TRAILER_WIDTH, config.TRAILER_HEIGHT

BG = (15, 17, 22)
PANEL = (24, 28, 37)
TEXT = (236, 240, 247)
MUTED = (138, 147, 166)
ACCENT = (108, 140, 255)
GREEN = (89, 217, 179)
RED = (255, 90, 95)
ORANGE = (255, 159, 67)
PURPLE = (190, 140, 255)


def _category_color(category: str) -> tuple[tuple[int, int, int], str]:
    c = (category or "").lower()
    if any(k in c for k in ("park", "trail", "outdoor", "canyon", "garden", "beach")):
        return GREEN, "#59d9b3"
    if any(k in c for k in ("metro", "train", "subway", "station", "transit", "bus", "rail")):
        return ACCENT, "#6c8cff"
    if any(k in c for k in ("food", "restaurant", "cafe", "coffee", "grocery", "market", "dining", "bar")):
        return ORANGE, "#ff9f43"
    if any(k in c for k in ("shop", "store", "mall", "retail")):
        return PURPLE, "#be8cff"
    return MUTED, "#8a93a6"


# ----------------------------- geocoding -----------------------------

@lru_cache(maxsize=128)
def geocode(query: str) -> tuple[float, float] | None:
    """Resolve a place name to (lat, lon) via OSM Nominatim, or None."""
    if not query.strip():
        return None
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "json", "limit": 1},
            headers={"User-Agent": config.GEOCODER_USER_AGENT},
            timeout=15,
        )
        time.sleep(1.1)  # be polite to the public geocoder
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return None
        return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as exc:  # noqa: BLE001
        print(f"[infocards] geocode failed for {query!r}: {exc}")
        return None


# --------------------------- drawing helpers ---------------------------

def _new_canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), BG)
    return img, ImageDraw.Draw(img)


def _center_text(draw, y, text, font, fill):
    w = draw.textlength(text, font=font)
    draw.text(((W - w) / 2, y), text, font=font, fill=fill)


def _star(draw, cx, cy, r, fill):
    """Draw a filled 5-point star centered at (cx, cy)."""
    import math

    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        rad = r if i % 2 == 0 else r * 0.42
        pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
    draw.polygon(pts, fill=fill)


def _kicker(draw, text, y=150):
    """Small uppercase label centered near the top."""
    f = _font(34)
    _center_text(draw, y, text.upper(), f, ACCENT)


# ------------------------------ cards ---------------------------------

def render_map_card(out_png: str, *, location_query: str, pois: list[dict], title: str) -> str | None:
    """Real map of the area with nearby spots + a labeled legend below."""
    try:
        from staticmap import CircleMarker, StaticMap
    except Exception:
        return None

    center = geocode(location_query) if location_query else None
    located: list[dict] = []
    for p in pois[:5]:
        name = str(p.get("name", "")).strip()
        if not name:
            continue
        q = f"{name}, {location_query}" if location_query else name
        ll = geocode(q)
        if ll:
            located.append({**p, "lat": ll[0], "lon": ll[1]})

    pts = [center] if center else []
    pts += [(d["lat"], d["lon"]) for d in located]
    if len(pts) < 1:
        return None

    # Landscape layout: map fills the left, a legend panel sits on the right.
    map_w = int(W * 0.62)
    legend_x = map_w
    try:
        m = StaticMap(map_w, H, url_template=config.MAP_TILE_URL)
        for d in located:
            _, hexc = _category_color(d.get("category", ""))
            m.add_marker(CircleMarker((d["lon"], d["lat"]), "white", 26))
            m.add_marker(CircleMarker((d["lon"], d["lat"]), hexc, 18))
        if center:
            m.add_marker(CircleMarker((center[1], center[0]), "white", 34))
            m.add_marker(CircleMarker((center[1], center[0]), "#ff5a5f", 26))
        zoom = None if len(pts) > 1 else 14
        map_img = m.render(zoom=zoom).convert("RGB")
    except Exception as exc:  # noqa: BLE001
        print(f"[infocards] map render failed: {exc}")
        return None

    img, draw = _new_canvas()
    img.paste(map_img, (0, 0))

    # Title pill over the map (centered on the map area).
    tfont = _font(46)
    tw = draw.textlength(title, font=tfont)
    pad = 26
    map_cx = map_w / 2
    draw.rounded_rectangle(
        [map_cx - tw / 2 - pad, 56, map_cx + tw / 2 + pad, 56 + 46 + pad],
        radius=18, fill=(10, 12, 16),
    )
    draw.text((map_cx - tw / 2, 56 + pad / 2), title, font=tfont, fill=TEXT)

    # Legend panel on the right.
    draw.rectangle([legend_x, 0, W, H], fill=PANEL)
    lx = legend_x + 50
    y = 80
    _kicker_left = _font(30)
    draw.text((lx, y), "WHAT'S NEARBY", font=_kicker_left, fill=ACCENT)
    y += 70

    if location_query:
        draw.ellipse([lx, y + 12, lx + 26, y + 38], fill=RED)
        nf, sf = _font(40), _font(26)
        draw.text((lx + 46, y), str(location_query)[:26], font=nf, fill=TEXT)
        draw.text((lx + 46, y + 46), "YOUR STAY", font=sf, fill=MUTED)
        y += 108

    nf, sf = _font(36), _font(26)
    for d in located:
        rgb, _ = _category_color(d.get("category", ""))
        draw.ellipse([lx, y + 12, lx + 24, y + 36], fill=rgb)
        name = str(d.get("name", ""))[:28]
        draw.text((lx + 46, y), name, font=nf, fill=TEXT)
        note = str(d.get("note", "") or d.get("category", "")).strip()
        if note:
            draw.text((lx + 46, y + 42), note[:30], font=sf, fill=MUTED)
        y += 92
        if y > H - 80:
            break

    img.save(out_png)
    return out_png


def render_neighborhood_card(out_png: str, *, title: str, pois: list[dict], location: str = "") -> str | None:
    """Text-only fallback when no map/geocoding is available."""
    items = [p for p in pois if str(p.get("name", "")).strip()]
    if not items and not location:
        return None
    img, draw = _new_canvas()
    _kicker(draw, "The Neighborhood", y=int(H * 0.13))
    _center_text(draw, int(H * 0.20), location or title, _font(60), TEXT)

    # Center the list block vertically in the lower half.
    rows = items[:5]
    step = 96
    y = int(H * 0.40)
    x = int(W * 0.30)
    nf, sf = _font(40), _font(28)
    for d in rows:
        rgb, _ = _category_color(d.get("category", ""))
        draw.ellipse([x, y + 14, x + 26, y + 40], fill=rgb)
        draw.text((x + 56, y), str(d.get("name", ""))[:34], font=nf, fill=TEXT)
        note = str(d.get("note", "") or d.get("category", "")).strip()
        if note:
            draw.text((x + 56, y + 48), note[:40], font=sf, fill=MUTED)
        y += step
        if y > H - 80:
            break
    img.save(out_png)
    return out_png


def render_price_card(out_png: str, *, price: str) -> str | None:
    if not str(price).strip():
        return None
    img, draw = _new_canvas()
    _kicker(draw, "Your Stay", y=H // 2 - 220)
    _center_text(draw, H // 2 - 120, str(price), _font(120), GREEN)
    img.save(out_png)
    return out_png


def render_rating_card(out_png: str, *, rating: dict) -> str | None:
    score = str(rating.get("score", "")).strip()
    reviews = str(rating.get("reviews", "")).strip()
    superhost = bool(rating.get("superhost"))
    extra = str(rating.get("note", "")).strip()
    if not score and not superhost and not extra:
        return None

    img, draw = _new_canvas()
    cy = H // 2 - 220
    if score:
        f = _font(130)
        tw = draw.textlength(score, font=f)
        star_r = 56
        gap = 34
        group_w = star_r * 2 + gap + tw
        x0 = (W - group_w) / 2
        _star(draw, x0 + star_r, cy + 66, star_r, GREEN)
        draw.text((x0 + star_r * 2 + gap, cy), score, font=f, fill=GREEN)
        cy += 170
    if reviews:
        _center_text(draw, cy, f"{reviews} reviews", _font(46), MUTED)
        cy += 90
    badge = extra or ("Superhost" if superhost else "")
    if badge:
        bf = _font(48)
        bw = draw.textlength(badge, font=bf)
        pad = 34
        draw.rounded_rectangle(
            [(W - bw) / 2 - pad, cy, (W + bw) / 2 + pad, cy + 48 + pad],
            radius=(48 + pad) // 2, fill=ACCENT,
        )
        _center_text(draw, cy + pad / 2, badge, bf, (255, 255, 255))
    img.save(out_png)
    return out_png


def make_info_card(info_type: str, data: dict, out_png: str) -> str | None:
    """Dispatch to the right renderer. Returns the PNG path or None to skip."""
    if info_type == "map":
        path = render_map_card(
            out_png,
            location_query=data.get("location_query", ""),
            pois=data.get("pois", []),
            title=data.get("title", "Explore the neighborhood"),
        )
        if path:
            return path
        # Fall back to a clean text list if the map couldn't be built.
        return render_neighborhood_card(
            out_png,
            title="The Neighborhood",
            pois=data.get("pois", []),
            location=data.get("location_query", ""),
        )
    if info_type == "neighborhood":
        return render_neighborhood_card(
            out_png, title="The Neighborhood",
            pois=data.get("pois", []), location=data.get("location_query", ""),
        )
    if info_type == "price":
        return render_price_card(out_png, price=data.get("price", ""))
    if info_type == "rating":
        return render_rating_card(out_png, rating=data.get("rating", {}))
    return None
