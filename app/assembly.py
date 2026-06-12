"""Stitch generated clips into the final 'day in the life' MP4.

Renders a closing end card (address, lease window, AI disclosure) and
concatenates everything. Each clip already fades in/out, giving clean
dip-to-black transitions without a fragile cross-fade filtergraph.
"""

from __future__ import annotations

from pathlib import Path

from . import config
from .ffmpeg_utils import run_ffmpeg
from .text_overlay import render_end_card as render_end_card_png
from .text_overlay import render_trailer_end_card as render_trailer_end_card_png


def render_end_card(
    work_dir: Path,
    out_path: str,
    *,
    address: str,
    lease: str,
) -> str:
    """A calm closing card with the lease window + AI disclosure."""
    dur = config.END_CARD_DURATION
    fade = 0.6

    card_png = render_end_card_png(
        str(work_dir / "end_card.png"),
        title=address or "Your next home",
        lease=lease or "Available now",
    )

    vf = (
        f"fade=t=in:st=0:d={fade},"
        f"fade=t=out:st={dur - fade:.2f}:d={fade},format=yuv420p"
    )

    run_ffmpeg(
        [
            "-loop", "1", "-t", f"{dur}", "-i", card_png,
            "-vf", vf,
            "-r", str(config.VIDEO_FPS),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            out_path,
        ]
    )
    return out_path


def render_trailer_end_card(
    work_dir: Path,
    out_path: str,
    *,
    end_card: dict,
) -> str:
    """A vertical (9:16) closing card for the listing trailer."""
    dur = config.TRAILER_END_CARD_DURATION
    fade = 0.5

    card_png = render_trailer_end_card_png(
        str(work_dir / "trailer_end_card.png"),
        name=str(end_card.get("name") or "Your next stay"),
        location=str(end_card.get("location") or ""),
        highlight=str(end_card.get("highlight") or ""),
        cta=str(end_card.get("cta") or "Book your stay"),
    )

    vf = (
        f"fade=t=in:st=0:d={fade},"
        f"fade=t=out:st={dur - fade:.2f}:d={fade},format=yuv420p"
    )

    run_ffmpeg(
        [
            "-loop", "1", "-t", f"{dur}", "-i", card_png,
            "-vf", vf,
            "-r", str(config.VIDEO_FPS),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            out_path,
        ]
    )
    return out_path


def render_still_clip(png_path: str, out_path: str, duration: float, *, fade: float = 0.4) -> str:
    """Turn a full-frame PNG (info card) into a clip with fade in/out.

    Kept static (no zoom) so text/maps stay crisp. Sized to the trailer canvas.
    """
    w, h, fps = config.TRAILER_WIDTH, config.TRAILER_HEIGHT, config.VIDEO_FPS
    fade_out = max(0.0, duration - fade)
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
        f"fps={fps},fade=t=in:st=0:d={fade},fade=t=out:st={fade_out:.2f}:d={fade},"
        f"format=yuv420p"
    )
    run_ffmpeg(
        [
            "-loop", "1", "-t", f"{duration}", "-i", png_path,
            "-vf", vf,
            "-r", str(fps),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            out_path,
        ]
    )
    return out_path


def concat_clips(clip_paths: list[str], out_path: str, work_dir: Path) -> str:
    """Concatenate identically-encoded clips with the concat demuxer."""
    if not clip_paths:
        raise ValueError("No clips to concatenate")

    list_file = work_dir / "concat.txt"
    list_file.write_text(
        "".join(f"file '{Path(p).resolve()}'\n" for p in clip_paths),
        encoding="utf-8",
    )

    run_ffmpeg(
        [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c",
            "copy",
            out_path,
        ]
    )
    return out_path
