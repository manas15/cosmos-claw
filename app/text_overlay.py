"""Render text to transparent PNGs with Pillow.

We composite these over video with ffmpeg's ``overlay`` filter instead of using
``drawtext``. Homebrew's ffmpeg ships without freetype (so ``drawtext`` is
unavailable), and this approach also gives us nicer rounded caption chips and
reliable Unicode/word-wrapping without filter-string escaping.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import config


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if config.FONT_PATH:
        try:
            return ImageFont.truetype(config.FONT_PATH, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        cur = words[0]
        for w in words[1:]:
            trial = f"{cur} {w}"
            if draw.textlength(trial, font=font) <= max_width:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


def render_chip(
    text: str,
    out_path: str,
    *,
    font_size: int = 34,
    max_text_width: int = 1040,
    pad_x: int = 24,
    pad_y: int = 14,
    box_alpha: int = 140,
    radius: int = 14,
) -> str:
    """A rounded, semi-transparent caption chip sized to its text."""
    font = _font(font_size)
    probe = Image.new("RGBA", (10, 10))
    d = ImageDraw.Draw(probe)
    lines = _wrap(d, text or " ", font, max_text_width)

    line_h = font_size + 8
    text_w = max((d.textlength(ln, font=font) for ln in lines), default=0)
    text_w = int(text_w)
    block_h = line_h * len(lines)

    w = text_w + pad_x * 2
    h = block_h + pad_y * 2
    img = Image.new("RGBA", (max(w, 1), max(h, 1)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=(0, 0, 0, box_alpha))

    y = pad_y
    for ln in lines:
        lw = draw.textlength(ln, font=font)
        x = (w - lw) / 2
        draw.text((x, y), ln, font=font, fill=(255, 255, 255, 255))
        y += line_h

    img.save(out_path)
    return out_path


def render_pill(
    text: str,
    out_path: str,
    *,
    font_size: int = 44,
    pad_x: int = 30,
    pad_y: int = 16,
    fill: tuple[int, int, int, int] = (255, 255, 255, 235),
    text_color: tuple[int, int, int, int] = (16, 18, 24, 255),
    max_text_width: int = 900,
) -> str:
    """A solid, high-contrast rounded title pill (for the opening hook)."""
    font = _font(font_size)
    probe = Image.new("RGBA", (10, 10))
    d = ImageDraw.Draw(probe)
    lines = _wrap(d, text or " ", font, max_text_width)

    line_h = font_size + 8
    text_w = int(max((d.textlength(ln, font=font) for ln in lines), default=0))
    block_h = line_h * len(lines)

    w = text_w + pad_x * 2
    h = block_h + pad_y * 2
    img = Image.new("RGBA", (max(w, 1), max(h, 1)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=h // 2, fill=fill)

    y = pad_y
    for ln in lines:
        lw = draw.textlength(ln, font=font)
        draw.text(((w - lw) / 2, y), ln, font=font, fill=text_color)
        y += line_h

    img.save(out_path)
    return out_path


def render_trailer_end_card(
    out_path: str,
    *,
    name: str,
    location: str = "",
    highlight: str = "",
    cta: str = "",
    disclosure: str = "AI-generated preview · Cosmos Claw",
) -> str:
    """A full-frame VERTICAL closing card for the listing trailer."""
    w, h = config.TRAILER_WIDTH, config.TRAILER_HEIGHT
    img = Image.new("RGB", (w, h), (15, 17, 22))
    draw = ImageDraw.Draw(img)

    name_font = _font(78)
    loc_font = _font(44)
    hi_font = _font(40)
    cta_font = _font(52)
    disc_font = _font(28)

    cy = h // 2 - 240

    name_lines = _wrap(draw, name or "Your next stay", name_font, w - 160)
    for ln in name_lines:
        lw = draw.textlength(ln, font=name_font)
        draw.text(((w - lw) / 2, cy), ln, font=name_font, fill=(244, 247, 252))
        cy += 92
    cy += 18

    if location:
        lw = draw.textlength(location, font=loc_font)
        draw.text(((w - lw) / 2, cy), location, font=loc_font, fill=(138, 147, 166))
        cy += 70

    if highlight:
        for ln in _wrap(draw, highlight, hi_font, w - 220):
            lw = draw.textlength(ln, font=hi_font)
            draw.text(((w - lw) / 2, cy), ln, font=hi_font, fill=(89, 217, 179))
            cy += 56
    cy += 40

    cta_text = cta or "Book your stay"
    # Accent CTA pill.
    cta_w = int(draw.textlength(cta_text, font=cta_font))
    pill_w, pill_h = cta_w + 120, 110
    px = (w - pill_w) // 2
    draw.rounded_rectangle(
        [px, cy, px + pill_w, cy + pill_h], radius=pill_h // 2, fill=(108, 140, 255)
    )
    cw = draw.textlength(cta_text, font=cta_font)
    draw.text(((w - cw) / 2, cy + (pill_h - 52) / 2 - 6), cta_text, font=cta_font, fill=(255, 255, 255))

    dw = draw.textlength(disclosure, font=disc_font)
    draw.text(((w - dw) / 2, h - 120), disclosure, font=disc_font, fill=(120, 129, 148))

    img.save(out_path)
    return out_path


def render_end_card(
    out_path: str,
    *,
    title: str,
    lease: str,
    disclosure: str = "AI-enhanced lifestyle preview",
) -> str:
    """A full-frame closing card."""
    w, h = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
    img = Image.new("RGB", (w, h), (17, 20, 24))
    draw = ImageDraw.Draw(img)

    title_font = _font(48)
    lease_font = _font(34)
    disc_font = _font(24)

    title_lines = _wrap(draw, title or "Your next home", title_font, w - 200)

    # Title block centered vertically a bit above middle.
    ty = h // 2 - 130
    for ln in title_lines:
        lw = draw.textlength(ln, font=title_font)
        draw.text(((w - lw) / 2, ty), ln, font=title_font, fill=(240, 244, 250))
        ty += 58

    lease_text = lease or "Available now"
    lw = draw.textlength(lease_text, font=lease_font)
    draw.text(((w - lw) / 2, ty + 16), lease_text, font=lease_font, fill=(108, 217, 179))

    dw = draw.textlength(disclosure, font=disc_font)
    draw.text(((w - dw) / 2, h - 84), disclosure, font=disc_font, fill=(138, 147, 166))

    img.save(out_path)
    return out_path
